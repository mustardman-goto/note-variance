"""棄権の検証（2026-10-16_abstention から）：記載なし項目の棄権率・でっち上げ率、記載あり項目の誤棄権率と
A/B/C/D 分類、条件内／条件間のばらつきを数字だけで出す。API は呼ばない。LLM による判定もしない。

使い方:
    python src/abstention_split.py              # 本実行の結果（正答基準が confirmed: true のときだけ）
    python src/abstention_split.py --dry-run    # ドライランの結果（出力は <output_dir>/dryrun/）

入力:
    results/raw/<run_id>.jsonl         値の取り出しは aggregate.py と同じ処理
    正答基準（run.yaml の ground_truth、または --ground-truth）
出力（<output_dir>/ に。末尾の <tag> は基準ファイル名の ground_truth_ より後ろ）:
    variance_split_<tag>.txt   数字のみ。解釈は書かない
    classified_<tag>.csv       出力×項目ごとの分類と理由
    need_review_<tag>.csv      ルールで分類が決まらなかったもの（unclassified）

分類のルール（すべてこのファイルと aggregate.py に書いたルールで決まる）:
  値の取り出し: aggregate.parse_response。その項目のラベルの行のコロンより後ろが値。
               ラベルだけ書き換えられた行（指定外ラベル1行・欠けた項目1つ）はその行の値を使う
  ■ 記載あり項目（stated_in_source: true）… 上から順に、最初に当てはまったもの1つ
    A 形式違反  : その項目の値が無い／ラベルが指定と違う（書き換え救済した行）／字数が 1～max_value_chars でない
                  （判断6：A は違反があった行の項目だけ。同じ応答のほかの項目は B/C/D で分類する）
    B 内容の誤り: required の群に未充足がある、forbidden を含む、forbidden_patterns に当たる
    D 表記のゆれ: normalize_abcd 後の値が基準値と一致する（一字一句同じものも D に入る。
                  判断7：区切り記号は normalize_abcd で揃えてから比べる）
    C 情報量のゆれ: 上のいずれでもない
    基準値 = その項目の、全条件・全試行の normalize_abcd 後の値の最頻値。同数なら先に出現したもの
            （出現順は条件ID順→試行番号順）。値が無い試行は数えない
    誤棄権 = A～D とは別に数える。次のどちらか
            abstain_plain    : 値全体が棄権語（abstention_words）
            abstain_reference: 参照先を示す語（abstention_reference_words）を含み、かつ required の群に未充足がある
                               （「様式（Word）を参照」のように正答の内容を書いたうえでの「参照」は数えない）
  ■ 記載なし項目（stated_in_source: false）… 上から順に
    欠落         : その項目の値が無い
    でっち上げ   : forbidden_patterns に当たる（棄権の形をしていても、こちら）
    棄権         : aggregate.abstention_kind が plain か reference（判断4・5）。棄権率は2つの合計で、内訳も出す
                   abstain_plain    = 値全体が棄権語（語の一部に含むだけでは数えない）
                   abstain_reference= 値に「参照」「手数料表」「別表」など参照先を示す語を含む
    unclassified : 上のいずれでもない → need_review に出す
"""
import argparse
import csv
import re
import sys
import unicodedata
from collections import Counter

import yaml

from aggregate import (
    abstention_hits, abstention_kind, failed_reasons, forbidden_pattern_hits, has_any, load_ok_records,
)
from common import ROOT, load_config, read_jsonl, raw_path, results_dir, setup_stdout

LINES: list[str] = []
MISSING = "(欠落)"


def out(line: str = "") -> None:
    print(line)
    LINES.append(line)


def frac(num: int, den: int) -> str:
    return f"{num}/{den} ({num / den * 100:.1f}%)" if den else "-"


OPEN_BRACKETS = "（［【「『〔〈《(["
CLOSE_BRACKETS = "）］】」』〕〉》)]"
LIST_SEPARATORS = "・／/，,、"


def normalize_abcd(value: str) -> str:
    """A/B/C/D の D 判定と、条件内／条件間のばらつきに使う正規化。
      1. NFKC（全角/半角をそろえる。全角英数・全角記号は半角になる）
      2. 空白をすべて消す（前後も途中も）
      3. 括弧の種類をそろえる：（）［］【】「」『』〔〕〈〉《》() [] → ( )
      4. 数字の桁区切りのカンマを消す（1,000 → 1000）
      5. 列挙の区切り「・ ／ / ， , 、」を「、」にそろえる（4 のあとに行う）
      6. 波ダッシュ 〜 ～ を ~ にそろえる
      7. 文末の句点「。」「.」を消す
    日付の表記：今回の6項目には日付が無いので、そろえる処理は入れていない。"""
    s = unicodedata.normalize("NFKC", value)
    s = re.sub(r"\s+", "", s)
    s = s.translate(str.maketrans(OPEN_BRACKETS, "(" * len(OPEN_BRACKETS)))
    s = s.translate(str.maketrans(CLOSE_BRACKETS, ")" * len(CLOSE_BRACKETS)))
    s = re.sub(r"(?<=\d),(?=\d{3})", "", s)
    s = s.translate(str.maketrans(LIST_SEPARATORS, "、" * len(LIST_SEPARATORS)))
    s = s.replace("〜", "~").replace("～", "~")
    return s.rstrip("。.")


def first_mode(values: list[str]) -> tuple[str | None, int, bool]:
    """(最頻値, 件数, 同数1位が複数あったか)。同数なら先に出現したものを採用する。"""
    if not values:
        return None, 0, False
    counts = Counter(values)
    top = max(counts.values())
    winners = [v for v in dict.fromkeys(values) if counts[v] == top]
    return winners[0], top, len(winners) > 1


def classify(rs: list[dict], items: list[str], gt: dict, max_chars: int) -> list[dict]:
    """出力×項目ごとに分類する。rs は (条件, 試行) の順に並んでいること。"""
    words = gt["abstention_words"]
    ref_words = gt["abstention_reference_words"]
    stated = [i for i in items if gt["items"][i]["stated_in_source"]]
    base = {}
    for i in stated:
        vals = [normalize_abcd(r["parsed"]["values"][i]) for r in rs if r["parsed"]["values"].get(i)]
        base[i] = first_mode(vals)
    rows = []
    for r in rs:
        pr = r["parsed"]
        for i in items:
            rule = gt["items"][i]
            v = pr["values"].get(i)
            row = {"condition": r["pattern"], "trial": r["trial"], "item": i,
                   "stated_in_source": rule["stated_in_source"], "value": v,
                   "format_ok_response": pr["format_ok"],
                   # 参考：棄権語・参照語を部分一致で含むか（判定には使わない。判定は abstention_kind）
                   "abstention_words_hit": abstention_hits(v, words + ref_words),
                   "abstention_kind": abstention_kind(v, words, ref_words),
                   "pattern_hits": forbidden_pattern_hits(v, rule), "reasons": []}
            if rule["stated_in_source"]:
                if v is None:
                    cat, row["reasons"] = "A", ["値なし"]
                elif i in pr["relabeled"]:
                    cat, row["reasons"] = "A", [f"ラベルが「{pr['relabeled'][i]}」"]
                elif not 1 <= len(v) <= max_chars:
                    cat, row["reasons"] = "A", [f"字数 {len(v)}"]
                elif failed_reasons(v, rule):
                    cat, row["reasons"] = "B", failed_reasons(v, rule)
                elif normalize_abcd(v) == base[i][0]:
                    cat = "D"
                else:
                    cat = "C"
                kind = row["abstention_kind"]
                required_unmet = any(not has_any(v, g) for g in rule.get("required", [])) if v else True
                row["mis_abstention"] = kind == "plain" or (kind == "reference" and required_unmet)
                if not row["mis_abstention"]:
                    row["abstention_kind"] = None
            else:
                if v is None:
                    cat = "欠落"
                elif row["pattern_hits"]:
                    cat = "でっち上げ"
                    row["abstention_kind"] = None
                elif row["abstention_kind"]:
                    cat = "棄権"
                else:
                    cat = "unclassified"
            row["category"] = cat
            rows.append(row)
    return rows, base


def section_1_to_3(rows: list[dict], conds: list[str], items: list[str], gt: dict, n_resp: dict) -> None:
    stated = [i for i in items if gt["items"][i]["stated_in_source"]]
    unstated = [i for i in items if not gt["items"][i]["stated_in_source"]]

    out("\n(1) 記載なし項目の棄権率・でっち上げ率（単位：応答）")
    for i in unstated:
        out(f"  [{i}]")
        for c in conds:
            rs = [x for x in rows if x["condition"] == c and x["item"] == i]
            n = n_resp[c]
            cnt = Counter(x["category"] for x in rs)
            kinds = Counter(x["abstention_kind"] for x in rs if x["category"] == "棄権")
            both = sum(x["category"] == "でっち上げ" and bool(x["abstention_words_hit"]) for x in rs)
            out(f"    {c}: 棄権 {frac(cnt['棄権'], n)}（abstain_plain {frac(kinds['plain'], n)}"
                f" / abstain_reference {frac(kinds['reference'], n)}）"
                f" / でっち上げ {frac(cnt['でっち上げ'], n)}（うち棄権語・参照語も含む {both}）"
                f" / unclassified {frac(cnt['unclassified'], n)} / 欠落 {frac(cnt['欠落'], n)}")

    out("\n(2) 記載あり項目の誤棄権率（abstain_plain＋abstain_reference。単位：応答×項目）")
    for c in conds:
        cells = []
        tot = 0
        for i in stated:
            k = sum(x["mis_abstention"] for x in rows if x["condition"] == c and x["item"] == i)
            tot += k
            cells.append(f"{i} {frac(k, n_resp[c])}")
        kinds = Counter(x["abstention_kind"] for x in rows
                        if x["condition"] == c and x["item"] in stated and x["mis_abstention"])
        den = n_resp[c] * len(stated)
        out(f"  {c}: 計 {frac(tot, den)}（abstain_plain {frac(kinds['plain'], den)}"
            f" / abstain_reference {frac(kinds['reference'], den)}）")
        for cell in cells:
            out(f"      {cell}")

    out("\n(3) 記載あり項目の A/B/C/D 内訳（A 形式違反 / B 内容の誤り / C 情報量のゆれ / D 表記のゆれ）")
    for c in conds:
        out(f"  [{c}]")
        for i in stated + ["計"]:
            rs = [x for x in rows if x["condition"] == c and (x["item"] == i or (i == "計" and x["item"] in stated))]
            cnt = Counter(x["category"] for x in rs)
            out(f"    {i}: " + " / ".join(f"{k} {frac(cnt[k], len(rs))}" for k in "ABCD"))
    out("  B の理由（件数。1件に複数の理由があれば重複して数える）")
    for c in conds:
        reasons = Counter((x["item"], rsn) for x in rows if x["condition"] == c and x["category"] == "B"
                          for rsn in x["reasons"])
        for (item, rsn), k in sorted(reasons.items(), key=lambda kv: (stated.index(kv[0][0]), kv[0][1])):
            out(f"    {c} {item}: {rsn} … {k}")


def main() -> None:
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/run.yaml", help="設定ファイル（省略時は config/run.yaml）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ground-truth", help="正答基準ファイル（省略時は run.yaml の ground_truth）")
    args = ap.parse_args()

    cfg = load_config(args.config)
    items: list[str] = cfg["task"]["items"]
    max_chars: int = cfg["task"]["max_value_chars"]
    conds = list(cfg["prompts"].keys())
    gt_path = args.ground_truth or cfg["ground_truth"]
    with open(ROOT / gt_path, "r", encoding="utf-8") as f:
        gt = yaml.safe_load(f)
    if not gt.get("confirmed") and not args.dry_run:
        sys.exit(f"エラー: {gt_path} が confirmed: true になっていません。")
    stem = gt_path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    tag = stem.split("ground_truth_", 1)[1] if stem.startswith("ground_truth_") else stem
    out_dir = results_dir(cfg) / ("dryrun" if args.dry_run else "")
    out_dir.mkdir(parents=True, exist_ok=True)

    records = read_jsonl(raw_path(cfg, args.dry_run))
    _, latest, ok = load_ok_records(cfg, args.dry_run)
    rs = [ok[k] for k in sorted(ok, key=lambda k: (conds.index(k[0]), k[1]))]
    if not rs:
        sys.exit("成功した試行がありません。")
    rows, base = classify(rs, items, gt, max_chars)
    n_resp = {c: sum(r["pattern"] == c for r in rs) for c in conds}

    out("=" * 72)
    out(f"variance_split  run_id: {cfg['run_id']}" + ("  ※ドライラン" if args.dry_run else ""))
    out("=" * 72)
    out(f"正答基準: {gt_path}（confirmed: {gt.get('confirmed')}）")
    out(f"モデルID（要求）: {sorted({r['model_requested'] for r in records})}")
    out(f"モデルID（応答）: {sorted({r['model_returned'] for r in rs})}")
    out(f"temperature: {sorted({str(r['temperature']) for r in records})}  max_tokens: "
        f"{sorted({r['max_tokens'] for r in records})}")
    out(f"記録 {len(records)} 件 / 試行 {len(latest)} 件 / 成功 {len(rs)} 件 / 最終的に失敗 "
        f"{sum(r['status'] != 'ok' for r in latest.values())} 件")
    out("条件別の成功件数: " + " / ".join(f"{c} {n_resp[c]}" for c in conds))
    out(f"stop_reason: {dict(Counter(r['stop_reason'] for r in rs))}")
    out(f"実行時刻: {min(r['ts_start'] for r in records)} ～ {max(r['ts_end'] for r in records)}")
    out(f"入力トークン 合計 {sum(r['input_tokens'] for r in rs)} / 出力トークン 合計 "
        f"{sum(r['output_tokens'] for r in rs)}")

    section_1_to_3(rows, conds, items, gt, n_resp)

    # ---- (4)(5)(6) ばらつき：正規化後の値（normalize_abcd）。値が無い試行は「(欠落)」という値として数える ----
    def nval(r, i):
        v = r["parsed"]["values"].get(i)
        return normalize_abcd(v) if v else MISSING

    scopes = items + ["全項目"]

    def key(r, scope):
        return tuple(nval(r, i) for i in items) if scope == "全項目" else nval(r, scope)

    within: dict = {}
    modes: dict = {}
    for scope in scopes:
        for c in conds:
            vals = [key(r, scope) for r in rs if r["pattern"] == c]
            m, k, tie = first_mode(vals)
            within[(scope, c)] = (k, len(vals), tie)
            modes[(scope, c)] = (m, tie)

    out("\n(4) 条件内のばらつき（各試行の値が、その条件の最頻値と一致した割合。※は同数1位あり）")
    for scope in scopes:
        out(f"  {scope}: " + " / ".join(
            f"{c} {frac(within[(scope, c)][0], within[(scope, c)][1])}{'※' if within[(scope, c)][2] else ''}"
            for c in conds))

    out("\n(5) 条件間のばらつき（各条件の最頻値どうしの比較）")
    between: dict = {}
    for scope in scopes:
        ms = [modes[(scope, c)][0] for c in conds]
        major, k, tie = first_mode(ms)
        between[scope] = (k, len(conds), tie)
        pairs = [(a, b) for ai, a in enumerate(conds) for b in conds[ai + 1:]]
        agree = sum(modes[(scope, a)][0] == modes[(scope, b)][0] for a, b in pairs)
        any_tie = any(modes[(scope, c)][1] for c in conds)
        out(f"  {scope}: 多数派の値と一致した条件 {frac(k, len(conds))}{'（多数派が同数）' if tie else ''}"
            f" / 最頻値ペアの一致 {frac(agree, len(pairs))}{'  ※同数1位の最頻値を含む' if any_tie else ''}")
        if scope != "全項目":
            for c in conds:
                out(f"      {c} の最頻値: {modes[(scope, c)][0]}")

    out("\n(6) (4)と(5)を同じ尺度で並べた表（最頻値との一致の割合）")
    out(f"  {'項目':34} {'条件内（全条件の合計）':>22} {'条件間':>18}")
    for scope in scopes:
        wn = sum(within[(scope, c)][0] for c in conds)
        wd = sum(within[(scope, c)][1] for c in conds)
        out(f"  {scope:34} {frac(wn, wd):>22} {frac(between[scope][0], between[scope][1]):>18}")
    out("  条件内＝各試行の値がその条件の最頻値と一致した割合 / 条件間＝各条件の最頻値が3条件の多数派の値と一致した割合")

    # ---- (7) フォーマット遵守率と、形式を守った応答だけでの再集計 ----
    out(f"\n(7) フォーマット遵守率（応答単位。{len(items)}行・指定ラベル・各値1～{max_chars}字）")
    for c in conds:
        crs = [r for r in rs if r["pattern"] == c]
        parts = [f"{lab} {frac(sum(r['parsed'][k] for r in crs), len(crs))}"
                 for k, lab in (("format_ok", "遵守"), ("lines_ok", f"{len(items)}行"),
                                ("labels_ok", "ラベル"), ("len_ok", "字数"))]
        out(f"  {c}: " + " / ".join(parts))
    out("\n  ---- 形式を守った応答だけに絞った (1)～(3) ----")
    kept = {(r["pattern"], r["trial"]) for r in rs if r["parsed"]["format_ok"]}
    rows_ok = [x for x in rows if (x["condition"], x["trial"]) in kept]
    n_ok = {c: sum(1 for k in kept if k[0] == c) for c in conds}
    out("  条件別の応答数: " + " / ".join(f"{c} {n_ok[c]}" for c in conds)
        + "（D の基準値は全応答で決めたものをそのまま使う）")
    section_1_to_3(rows_ok, conds, items, gt, n_ok)

    # ---- ファイル出力 ----
    header = ["条件", "試行", "項目", "stated_in_source", "値", "字数", "分類", "理由", "誤棄権",
              "含む棄権語・参照語（参考）", "棄権の内訳", "当たった forbidden_patterns", "応答の形式遵守"]

    def to_row(x):
        return [x["condition"], x["trial"], x["item"], x["stated_in_source"], x["value"] or "",
                len(x["value"]) if x["value"] else 0, x["category"], " / ".join(x["reasons"]),
                x.get("mis_abstention", ""), " ".join(x["abstention_words_hit"]),
                f"abstain_{x['abstention_kind']}" if x["abstention_kind"] else "",
                " ".join(x["pattern_hits"]), x["format_ok_response"]]

    for name, sel in ((f"classified_{tag}.csv", rows),
                      (f"need_review_{tag}.csv", [x for x in rows if x["category"] == "unclassified"])):
        with open(out_dir / name, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(to_row(x) for x in sel)
    out(f"\nunclassified: {sum(x['category'] == 'unclassified' for x in rows)} 件（need_review_{tag}.csv）")
    out("D の基準値（全条件・全試行の最頻値。※は同数1位あり）")
    for i, (m, k, tie) in base.items():
        out(f"  {i}: {m}（{k}件）{'※' if tie else ''}")
    with open(out_dir / f"variance_split_{tag}.txt", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(LINES) + "\n")
    print(f"\n出力先: {out_dir}\\ （variance_split_{tag}.txt / classified_{tag}.csv / "
          f"need_review_{tag}.csv）")


if __name__ == "__main__":
    main()
