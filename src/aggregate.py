"""プロンプトの揺れ検証：集計スクリプト。

使い方:
    python src/aggregate.py             # 本実行の結果を集計
    python src/aggregate.py --dry-run   # お試し実行の結果を集計（results/dryrun/ に出力）

出力:
    results/summary.csv    指標の一覧（Excel で開ける）
    results/responses.csv  1レスポンスごとの判定結果
    results/variants.csv   項目ごとの「出てきた値」の一覧（揺れの分類つき）
    results/report.txt     画面に出したレポートと同じもの
"""
import argparse
import csv
import re
import unicodedata
from collections import Counter, defaultdict
from itertools import combinations, product

import yaml

from common import ROOT, cost_usd, load_config, price_for, raw_path, read_jsonl, results_dir, setup_stdout

REPORT_LINES: list[str] = []


def out(line: str = "") -> None:
    print(line)
    REPORT_LINES.append(line)


def normalize(value: str) -> str:
    """表記の違いだけを吸収する（全角/半角、空白、波ダッシュ、文末の句点）。"""
    s = unicodedata.normalize("NFKC", value)
    s = re.sub(r"\s+", "", s)
    s = s.replace("〜", "~")
    return s.rstrip("。.")


def parse_response(text: str, items: list[str], max_chars: int) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    values: dict[str, str] = {}
    labeled_lines = 0
    halfwidth_colon = True
    unlabeled = []
    for ln in lines:
        for item in items:
            m = re.match(re.escape(item) + r"\s*([:：])\s*(.*)$", ln)
            if m:
                labeled_lines += 1
                if item not in values:
                    values[item] = m.group(2).strip()
                if m.group(1) != ":":
                    halfwidth_colon = False
                break
        else:
            unlabeled.append(ln)
    lines_ok = len(lines) == len(items)   # 行数＝項目数（2026w41 は4、2026-10-16_abstention は6）
    labels_ok = labeled_lines == len(lines) and set(values) == set(items)
    # ラベルだけ書き換えられた行の救済：指定外ラベルの行が1つ、欠けた項目も1つなら、
    # その行の値をその項目の値として扱う（ラベル違反は labels_ok=False のまま数える）
    relabeled = {}
    missing = [i for i in items if i not in values]
    if len(missing) == 1 and len(unlabeled) == 1:
        m = re.match(r"(.+?)\s*[:：]\s*(.*)$", unlabeled[0])
        if m:
            values[missing[0]] = m.group(2).strip()
            relabeled[missing[0]] = m.group(1)
    len_ok = len(values) == len(items) and all(1 <= len(v) <= max_chars for v in values.values())
    return {
        "values": values,
        "relabeled": relabeled,
        "n_lines": len(lines),
        "lines_ok": lines_ok,
        "labels_ok": labels_ok,
        "len_ok": len_ok,
        "format_ok": lines_ok and labels_ok and len_ok,
        "halfwidth_colon": halfwidth_colon and bool(values),
    }


def load_ground_truth(rel_path: str | None) -> dict | None:
    """正答基準を読む。rel_path は note-variance フォルダからの相対パス。"""
    if not rel_path or not (ROOT / rel_path).exists():
        return None
    with open(ROOT / rel_path, "r", encoding="utf-8") as f:
        gt = yaml.safe_load(f)
    return gt if gt and gt.get("confirmed") else None


def has_any(value: str | None, group: list[str]) -> bool:
    """グループ内の語のどれか1つでも値に含まれるか（全角/半角・空白の違いは無視）。"""
    return bool(value) and any(normalize(alt) in normalize(value) for alt in group)


def normalize_for_match(value: str) -> str:
    """forbidden_patterns（正規表現）を当てる前の正規化。NFKC だけを行う。
    全角数字・全角英字・全角記号は半角になる（「１０００円」→「1000円」）。
    空白は消さない（パターン側で \\s* と書いて吸収する）。桁区切りのカンマも消さない。"""
    return unicodedata.normalize("NFKC", value)


def forbidden_pattern_hits(value: str | None, rule: dict) -> list[str]:
    """forbidden_patterns のうち、値に当たったパターン（2026-10-16_abstention から）。
    判定ルール:
      - 照合する範囲は、その項目のラベルの行のコロンより後ろ（値）だけ。ほかの行は見ない
      - 値を normalize_for_match で正規化してから re.search で探す（部分一致。位置は問わない）
      - 1つでも当たれば forbidden と同じく不正解。棄権語を同時に含んでいても不正解
    """
    if not value:
        return []
    v = normalize_for_match(value)
    return [p for p in rule.get("forbidden_patterns", []) if re.search(p, v)]


ABSTENTION_STRIP = "「」『』\"'"


def abstention_hits(value: str | None, words: list[str]) -> list[str]:
    """値に含まれる棄権語。棄権語のリストは正答基準ファイル（abstention_words）から渡す。
    正規化は normalize（NFKC・空白を消す・波ダッシュ・文末の句点）を値と語の両方に行い、部分一致で探す。"""
    if not value:
        return []
    v = normalize(value)
    return [w for w in words if normalize(w) in v]


def is_whole_abstention(value: str | None, words: list[str]) -> bool:
    """値全体が棄権語そのものか。normalize のあと、かぎ括弧・引用符を消してから完全一致で比べる。"""
    if not value:
        return False
    v = normalize(value).strip(ABSTENTION_STRIP).rstrip("。.")
    return any(v == normalize(w) for w in words)


def abstention_kind(value: str | None, plain: list[str], reference: list[str]) -> str | None:
    """棄権の内訳（2026-10-16_abstention の正答基準の判断4・5）。
      "plain"    : 値全体が棄権語（plain）のどれかと一致（is_whole_abstention）。語の一部に含むだけでは数えない
      "reference": plain でなく、参照先を示す語（reference）のどれかを含む（abstention_hits。部分一致）
      None       : どちらでもない
    forbidden_patterns との優先順位はここでは見ない（呼び出し側で、当たれば不正解／でっち上げにする）。"""
    if is_whole_abstention(value, plain):
        return "plain"
    if abstention_hits(value, reference):
        return "reference"
    return None


def failed_reasons(value: str | None, rule: dict) -> list[str]:
    """正解条件のうち満たしていないものを列挙する。空リストなら正解。
    記載なし項目（abstention を持つ項目）は、棄権（abstention_kind が plain か reference）であることが正解条件。"""
    if not value:
        return ["値なし"]
    reasons = [f"required {group} をどれも含まない"
               for group in rule.get("required", []) if not has_any(value, group)]
    if "abstention" in rule and abstention_kind(
            value, rule["abstention"]["plain"], rule["abstention"]["reference"]) is None:
        reasons.append("棄権になっていない（値全体が棄権語でも、参照先を示す語を含むものでもない）")
    reasons += [f"forbidden「{ng}」を含む"
                for ng in rule.get("forbidden", []) if has_any(value, [ng])]
    reasons += [f"forbidden_patterns「{p}」に当たる" for p in forbidden_pattern_hits(value, rule)]
    return reasons


def is_correct(value: str | None, rule: dict) -> bool:
    """正解の条件：required の各グループから1つ以上を含み、forbidden を1つも含まず、
    forbidden_patterns に1つも当たらない。"""
    return not failed_reasons(value, rule)


def load_ok_records(cfg: dict, dry_run: bool = False) -> tuple[list[dict], dict, dict]:
    """生ログを読み、(全記録, 試行ごとの最後の記録, そのうち成功したもの) を返す。"""
    records = read_jsonl(raw_path(cfg, dry_run))
    latest: dict[tuple[str, int], dict] = {}
    for r in records:
        latest[(r["pattern"], r["trial"])] = r
    ok = {k: r for k, r in latest.items() if r["status"] == "ok"}
    for r in ok.values():
        r["parsed"] = parse_response(r["text"], cfg["task"]["items"], cfg["task"]["max_value_chars"])
    return records, latest, ok


def same(a: dict, b: dict, items: list[str], norm: bool) -> bool:
    """2つの出力で、指定した項目の値がすべて同じか。値が取れなかった項目は不一致扱い。"""
    for item in items:
        va, vb = a["parsed"]["values"].get(item), b["parsed"]["values"].get(item)
        if va is None or vb is None:
            return False
        if (normalize(va) != normalize(vb)) if norm else (va != vb):
            return False
    return True


def pair_rate(pairs: list[tuple[dict, dict]], items: list[str], norm: bool) -> tuple[int, int]:
    return sum(same(a, b, items, norm) for a, b in pairs), len(pairs)


def pct(num: int, den: int) -> str:
    return f"{num / den * 100:5.1f}% ({num}/{den})" if den else "   -   (0/0)"


def main() -> None:
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/run.yaml", help="設定ファイル（省略時は config/run.yaml）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--ground-truth", help="正答基準ファイル（省略時は run.yaml の ground_truth）")
    ap.add_argument("--tag", help="出力ファイル名の末尾に付ける名前。v1 なら summary_v1.csv になる")
    args = ap.parse_args()

    cfg = load_config(args.config)
    items: list[str] = cfg["task"]["items"]
    max_chars: int = cfg["task"]["max_value_chars"]
    patterns = list(cfg["prompts"].keys())
    records, latest, ok = load_ok_records(cfg, args.dry_run)
    out_dir = results_dir(cfg) / ("dryrun" if args.dry_run else "")
    out_dir.mkdir(parents=True, exist_ok=True)
    gt_path = args.ground_truth or cfg.get("ground_truth")
    gt = load_ground_truth(gt_path)
    # --tag が無ければ基準ファイル名から決める（ground_truth_v2.yaml → v2）。
    # 基準を変えて集計しても、別の基準で出した結果を上書きしないため
    stem = (gt_path or "").replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0]
    tag = args.tag or (stem.split("ground_truth_", 1)[1] if stem.startswith("ground_truth_") else None)
    suffix = f"_{tag}" if tag else ""
    summary_rows: list[list] = []

    def add(section, pattern, item, metric, num, den):
        summary_rows.append([section, pattern, item, metric,
                             f"{num / den:.4f}" if den else "", num, den])

    if not records:
        out("記録がありません。先に src/run.py を実行してください。")
        return

    # ---- 試行ごとに最後の記録を採用する（失敗→再開で成功した場合は成功を採用） ----
    by_pattern = {p: [ok[k] for k in sorted(ok) if k[0] == p] for p in patterns}
    by_pattern = {p: rs for p, rs in by_pattern.items() if rs or any(k[0] == p for k in latest)}
    all_ok = [r for rs in by_pattern.values() for r in rs]

    # ============ 0. 実行条件 ============
    out("=" * 72)
    out(f"プロンプトの揺れ検証レポート  run_id: {cfg['run_id']}" + ("  ※ドライラン" if args.dry_run else ""))
    out("=" * 72)
    out("\n■ 0. 実行条件（ログから集計）")
    out(f"  対象文書      : {cfg['document']['path']}")
    out(f"  正答基準      : {gt_path}" + ("" if gt is not None else "（未確定または無し）"))
    out(f"  モデルID      : 要求 {sorted({r['model_requested'] for r in records})} / "
        f"応答 {sorted({r['model_returned'] for r in all_ok})}")
    out(f"  temperature   : {sorted({str(r['temperature']) for r in records})}（API に送った値。None は未送信）")
    out(f"  effort        : {sorted({str(r['effort']) for r in records})}（None は未送信＝モデルの既定）")
    out(f"  実行時刻      : {min(r['ts_start'] for r in records)} ～ {max(r['ts_end'] for r in records)}")
    n_err_records = sum(r["status"] != "ok" for r in records)
    n_failed_trials = sum(r["status"] != "ok" for r in latest.values())
    n_retries = sum(len(r.get("retries", [])) for r in records)
    out(f"  試行          : {len(latest)} 件（成功 {len(ok)} / 最終的に失敗 {n_failed_trials}）")
    out(f"  失敗の記録    : {n_err_records} 件、リトライ {n_retries} 回")
    for r in records:
        if r["status"] != "ok":
            out(f"    - {r['pattern']} #{r['trial']}: {r['error']['type']} (HTTP {r['error']['status']})")
    if all_ok:
        tin = sum(r["input_tokens"] for r in all_ok)
        tout = sum(r["output_tokens"] for r in all_ok)
        out(f"  入力トークン  : 合計 {tin} / 1件 {min(r['input_tokens'] for r in all_ok)}～"
            f"{max(r['input_tokens'] for r in all_ok)}")
        out(f"  出力トークン  : 合計 {tout}（思考分を含む）")
        c = cost_usd(price_for(cfg, all_ok[0]["model_requested"]), tin, tout)
        out(f"  コスト        : {'$%.4f' % c if c is not None else '不明'}（成功分のみ。run.yaml の単価で試算）")
        out(f"  所要時間      : 1件平均 {sum(r['latency_sec'] for r in all_ok) / len(all_ok):.1f} 秒")
        stops = Counter(r["stop_reason"] for r in all_ok)
        out(f"  stop_reason   : {dict(stops)}")
    if not all_ok:
        out("\n成功した試行が無いため、以降の集計はできません。")
        return

    # ============ 1. フォーマット遵守率 ============
    n_items = len(items)
    out(f"\n■ 1. フォーマット遵守率（{n_items}行・指定ラベル・各値1～{max_chars}字）")
    out(f"  {'':4} {'遵守':>16} {f'{n_items}行':>16} {'ラベル':>16} {'字数':>16}")
    for p, rs in list(by_pattern.items()) + [("全体", all_ok)]:
        cells = []
        for key in ("format_ok", "lines_ok", "labels_ok", "len_ok"):
            num = sum(r["parsed"][key] for r in rs)
            cells.append(pct(num, len(rs)))
            add("1.フォーマット遵守", p, "", key, num, len(rs))
        out(f"  {p:4} " + " ".join(f"{c:>16}" for c in cells))
    over = [(r["pattern"], r["trial"], i, len(v)) for r in all_ok
            for i, v in r["parsed"]["values"].items() if len(v) > max_chars]
    for p, t, i, n in over:
        out(f"    字数超過: {p} #{t} 「{i}」{n}字")
    for r in all_ok:
        for i, used in r["parsed"]["relabeled"].items():
            out(f"    ラベル違反: {r['pattern']} #{r['trial']} 「{i}」→「{used}」と出力"
                "（値の比較と正誤には、この行の値を使う）")
    n_full = sum(not r["parsed"]["halfwidth_colon"] for r in all_ok)
    out(f"  （参考）コロンが半角「:」でない出力: {n_full} 件")

    # ============ 2・3. 揺れ（同じ尺度） ============
    within_pairs = {p: list(combinations(rs, 2)) for p, rs in by_pattern.items()}
    between_pairs = {(p, q): list(product(by_pattern[p], by_pattern[q]))
                     for p, q in combinations(by_pattern, 2)}
    all_within = [pr for prs in within_pairs.values() for pr in prs]
    all_between = [pr for prs in between_pairs.values() for pr in prs]

    out("\n■ 2・3. 出力の揺れ と 入力の揺れ（★同じ尺度で比較）")
    out("  尺度: 出力を2つ取り出したとき、値が一致しているペアの割合。")
    out("        「原文」は一字一句同じ、「正規化後」は全角半角・空白・句点の違いを無視。")
    out("        出力の揺れ＝同じプロンプトどうしのペア / 入力の揺れ＝違うプロンプトどうしのペア")
    out("        一致率が低いほど揺れが大きい。")
    out(f"\n  {'対象':22} {'':10} {'同一パターン内（出力の揺れ）':>22} {'パターン間（入力の揺れ）':>22} {'差':>8}")
    for label, scope in [(f"{len(items)}項目すべて", items)] + [(i, [i]) for i in items]:
        for norm, nlabel in ((False, "原文"), (True, "正規化後")):
            wn, wd = pair_rate(all_within, scope, norm)
            bn, bd = pair_rate(all_between, scope, norm)
            diff = f"{(wn / wd - bn / bd) * 100:+.1f}pt" if wd and bd else "-"
            out(f"  {label:22} {nlabel:10} {pct(wn, wd):>22} {pct(bn, bd):>22} {diff:>8}")
            item_name = "" if len(scope) > 1 else label
            add("2.同一パターン内一致率", "全体", item_name, f"ペア一致率_{nlabel}", wn, wd)
            add("3.パターン間一致率", "全体", item_name, f"ペア一致率_{nlabel}", bn, bd)
    out("  ※差 = 同一パターン内 − パターン間。プラスが大きいほど「プロンプトを変えた影響」が大きい。")

    out(f"\n  [内訳] 同一パターン内の一致率（{len(items)}項目すべて）と、出力の種類数")
    for p, prs in within_pairs.items():
        rn, rd = pair_rate(prs, items, False)
        nn, nd = pair_rate(prs, items, True)
        kinds = len({tuple(r["parsed"]["values"].get(i) for i in items) for r in by_pattern[p]})
        out(f"    {p}: 原文 {pct(rn, rd)}  正規化後 {pct(nn, nd)}  種類数 {kinds}/{len(by_pattern[p])}")
        add("2.同一パターン内一致率", p, "", "ペア一致率_原文", rn, rd)
        add("2.同一パターン内一致率", p, "", "ペア一致率_正規化後", nn, nd)
    out(f"\n  [内訳] パターン間の一致率（{len(items)}項目すべて）")
    for (p, q), prs in between_pairs.items():
        rn, rd = pair_rate(prs, items, False)
        nn, nd = pair_rate(prs, items, True)
        out(f"    {p}×{q}: 原文 {pct(rn, rd)}  正規化後 {pct(nn, nd)}")
        add("3.パターン間一致率", f"{p}x{q}", "", "ペア一致率_原文", rn, rd)
        add("3.パターン間一致率", f"{p}x{q}", "", "ペア一致率_正規化後", nn, nd)

    # ============ 4. 正答率 ============
    out("\n■ 4. 原文と照合した正答率")
    if gt is None:
        out("  未判定：config/ground_truth.yaml が未確定です（confirmed: true になっていません）。")
    else:
        for r in all_ok:
            r["correct"] = {i: is_correct(r["parsed"]["values"].get(i), gt["items"][i]) for i in items}
        out(f"  {'':4} " + " ".join(f"{i[:9]:>14}" for i in items) + f" {f'{len(items)}項目すべて':>14}")
        for p, rs in list(by_pattern.items()) + [("全体", all_ok)]:
            cells = []
            for i in items:
                num = sum(r["correct"][i] for r in rs)
                cells.append(pct(num, len(rs)))
                add("4.正答率", p, i, "正答率", num, len(rs))
            num = sum(all(r["correct"].values()) for r in rs)
            cells.append(pct(num, len(rs)))
            add("4.正答率", p, "", f"正答率_{len(items)}項目すべて", num, len(rs))
            out(f"  {p:4} " + " ".join(f"{c:>14}" for c in cells))

        # optional：基準ファイルにあるときだけ出す。正誤の判定には使わない
        if any(gt["items"][i].get("optional") for i in items):
            out("\n  [参考] optional の語を含む出力の割合（正誤の判定には使っていない）")
            for i in items:
                for group in gt["items"][i].get("optional", []):
                    num = sum(has_any(r["parsed"]["values"].get(i), group) for r in all_ok)
                    out(f"    {i} / {group[0]} など: {pct(num, len(all_ok))}")
                    add("4b.optional含有率(参考)", "全体", i, group[0], num, len(all_ok))

    # ============ 5. 揺れの分類 ============
    out("\n■ 5. 揺れた箇所の分類（単位：出力×項目。基準は項目ごとの最頻値）")
    out("  一致＝最頻値と一字一句同じ / 表記のゆれ＝正規化すれば同じ / 情報量のゆれ＝違うが正解条件は満たす")
    out("  内容の誤り＝正解条件を満たさない / 欠落＝その項目の行が無い / 未判定＝正解が未確定")
    variants_rows = []
    cats_total: Counter = Counter()
    for i in items:
        raw_counts = Counter(r["parsed"]["values"].get(i) for r in all_ok)
        mode = max((v for v in raw_counts if v is not None), key=lambda v: raw_counts[v], default=None)
        cats: Counter = Counter()
        value_cat: dict = {}
        for r in all_ok:
            v = r["parsed"]["values"].get(i)
            if v is None:
                cat = "欠落"
            elif gt is not None and not r["correct"][i]:
                cat = "内容の誤り"
            elif v == mode:
                cat = "一致"
            elif normalize(v) == normalize(mode):
                cat = "表記のゆれ"
            else:
                cat = "情報量のゆれ" if gt is not None else "未判定"
            r.setdefault("category", {})[i] = cat
            cats[cat] += 1
            value_cat[v] = cat
        cats_total.update(cats)
        out(f"  {i}: " + " / ".join(f"{c} {n}" for c, n in cats.most_common())
            + f"  （値の種類 {len(raw_counts)}）")
        for c, n in cats.items():
            add("5.揺れの分類", "全体", i, c, n, len(all_ok))
        for v, n in raw_counts.most_common():
            per_p = " ".join(f"{p}:{sum(r['parsed']['values'].get(i) == v for r in rs)}"
                             for p, rs in by_pattern.items())
            variants_rows.append([i, v if v is not None else "(欠落)", len(v) if v else 0, n, per_p,
                                  value_cat[v], "最頻値" if v == mode else ""])
    out("  合計: " + " / ".join(f"{c} {n}" for c, n in cats_total.most_common())
        + f"  （全 {len(all_ok) * len(items)} 箇所）")
    for c, n in cats_total.items():
        add("5.揺れの分類", "全体", "", c, n, len(all_ok) * len(items))
    out("  ※ルールによる自動分類です。results/variants.csv に全ての値を出しているので目視で確認できます。")

    # ============ ファイル出力（Excel で開けるよう BOM 付き UTF-8） ============
    def write_csv(name: str, header: list, rows: list) -> None:
        with open(out_dir / name.replace(".csv", suffix + ".csv"), "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    write_csv("summary.csv", ["区分", "パターン", "項目", "指標", "割合", "分子", "分母"], summary_rows)
    write_csv("variants.csv", ["項目", "値", "字数", "件数", "パターン別件数", "分類", "備考"], variants_rows)
    resp_rows = []
    for (p, t), r in sorted(latest.items()):
        row = [p, t, r["status"], r["ts_start"], r["model_requested"], r["temperature"],
               r.get("input_tokens", ""), r.get("output_tokens", ""), r["latency_sec"], r["api_calls"]]
        if r["status"] == "ok":
            pr = r["parsed"]
            row += [pr["format_ok"], pr["lines_ok"], pr["labels_ok"], pr["len_ok"]]
            for i in items:
                v = pr["values"].get(i)
                row += [v or "", len(v) if v else 0,
                        r["correct"][i] if gt is not None else "", r["category"][i]]
            row += [r["text"]]
        else:
            row += [""] * (4 + 4 * len(items)) + [f"{r['error']['type']}: {r['error']['message']}"]
        resp_rows.append(row)
    header = ["パターン", "試行", "状態", "開始時刻", "モデルID", "temperature", "入力トークン",
              "出力トークン", "秒", "API呼び出し回数", "遵守", f"{len(items)}行", "ラベル", "字数"]
    for i in items:
        header += [i, f"{i}_字数", f"{i}_正誤", f"{i}_分類"]
    write_csv("responses.csv", header + ["出力全文またはエラー"], resp_rows)

    out(f"\n出力先: {out_dir.relative_to(ROOT)}\\ （summary{suffix}.csv / responses{suffix}.csv / "
        f"variants{suffix}.csv / report{suffix}.txt）")
    with open(out_dir / f"report{suffix}.txt", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(REPORT_LINES) + "\n")


if __name__ == "__main__":
    main()
