"""揺れを「パターン内」と「パターン間」に分けて、最頻値との一致率で出す。API は呼ばない。

使い方:
    python src/variance_split.py
    python src/variance_split.py --tag v1 --count-item 応募期間 --count-token 令和7年

入力:
    results/raw/<run_id>.jsonl      値の取り出し（aggregate.py と同じ処理）
    results/responses_<tag>.csv     揺れの分類（aggregate.py --tag <tag> が出したもの）
出力:
    results/variance_split.txt      数字のみ。解釈は書かない
"""
import argparse
import csv
import sys
from collections import Counter

from aggregate import load_ok_records, normalize
from common import ROOT, load_config, setup_stdout

LINES: list[str] = []
ALL = "4項目すべて"


def out(line: str = "") -> None:
    print(line)
    LINES.append(line)


def frac(num: int, den: int) -> str:
    return f"{num}/{den} ({num / den * 100:.0f}%)" if den else "-"


def mode_of(values: list) -> tuple[object, int, bool]:
    """(最頻値, その件数, 同数1位が複数あるか)。"""
    ranked = Counter(values).most_common()
    tie = len(ranked) > 1 and ranked[0][1] == ranked[1][1]
    return ranked[0][0], ranked[0][1], tie


def main() -> None:
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="v1", help="揺れの分類に使う responses_<tag>.csv")
    ap.add_argument("--count-item", help="語の出現回数を数える項目（5番の表）")
    ap.add_argument("--count-token", help="数える語（正規化後の値の中で数える）")
    args = ap.parse_args()

    cfg = load_config()
    items: list[str] = cfg["task"]["items"]
    patterns = list(cfg["prompts"].keys())
    _, latest, ok = load_ok_records(cfg)
    scopes = items + [ALL]

    # 正規化後の値。値が取れなかった項目は None
    norm: dict[str, list[dict]] = {p: [] for p in patterns}
    for (p, t) in sorted(ok):
        v = ok[(p, t)]["parsed"]["values"]
        row = {i: (normalize(v[i]) if i in v else None) for i in items}
        row[ALL] = tuple(row[i] for i in items)
        row["_trial"] = t
        row["_raw"] = v
        norm[p].append(row)

    out("=" * 76)
    out(f"揺れの分解  run_id: {cfg['run_id']}")
    out("=" * 76)
    out(f"  対象: 成功した試行 {len(ok)} 件（失敗 {len(latest) - len(ok)} 件は対象外）。"
        + " ".join(f"{p}:{len(norm[p])}" for p in patterns))
    out("  値の比較は正規化後（NFKC で全角/半角を統一、空白を除去、文末の句点を除去）。")
    out("  最頻値 = その集合の中で最も多く出た値。")

    # ---------- 1 ----------
    out("\n■ 1. パターン内：そのパターンの最頻値と一致した件数")
    modes: dict[str, dict[str, object]] = {s: {} for s in scopes}
    hits: dict[str, dict[str, int]] = {s: {} for s in scopes}
    ties = []
    for s in scopes:
        for p in patterns:
            m, n, tie = mode_of([r[s] for r in norm[p]])
            modes[s][p], hits[s][p] = m, n
            if tie:
                ties.append((s, p, n))
    out(f"  {'項目':<14}" + "".join(f"{p:>14}" for p in patterns) + f"{'5パターン合計':>16}")
    for s in scopes:
        total = sum(hits[s].values())
        out(f"  {s:<14}" + "".join(f"{frac(hits[s][p], len(norm[p])):>14}" for p in patterns)
            + f"{frac(total, len(ok)):>16}")
    for s, p, n in ties:
        out(f"  ※同数1位あり: {s} / {p}（{n}件の値が複数。件数は同じなので上の表は変わらない）")

    out("\n  [各パターンの値の種類数（正規化後）]")
    out(f"  {'項目':<14}" + "".join(f"{p:>14}" for p in patterns))
    for s in scopes:
        out(f"  {s:<14}" + "".join(f"{len({r[s] for r in norm[p]}):>14}" for p in patterns))

    # ---------- 2 ----------
    out("\n■ 2. パターン間：各パターンの最頻値どうしの比較")
    between: dict[str, tuple[int, int]] = {}
    out(f"  {'項目':<14}{'最頻値の種類数':>10}{'5パターンで一致':>12}{'多数派と一致':>12}{'最頻値ペアの一致':>14}")
    for s in scopes:
        ms = [modes[s][p] for p in patterns]
        _, top, _ = mode_of(ms)
        pairs = [(a, b) for x, a in enumerate(ms) for b in ms[x + 1:]]
        same = sum(a == b for a, b in pairs)
        between[s] = (top, len(ms))
        out(f"  {s:<14}{len(set(ms)):>10}{'はい' if len(set(ms)) == 1 else 'いいえ':>12}"
            f"{frac(top, len(ms)):>14}{frac(same, len(pairs)):>16}")
    out("  多数派と一致 = 5つの最頻値のうち、最も多い値と同じだったパターンの数。")
    if any(sc in {s for s, _, _ in ties} for sc in scopes):
        out("  ※同数1位があるパターンでは、どの値を最頻値とするかで 2 の結果が変わりうる（1 の※参照）。")

    out("\n  [各パターンの最頻値（正規化後）]")
    for i in items:
        out(f"  {i}")
        for p in patterns:
            out(f"    {p} ({hits[i][p]}/{len(norm[p])}): {modes[i][p]}")

    # ---------- 3 ----------
    out("\n■ 3. 1 と 2 を同じ尺度（最頻値との一致率）で並べた表")
    out("  パターン内 = 各出力が、自分のパターンの最頻値と一致した割合（5パターン合計）")
    out("  パターン間 = 各パターンの最頻値が、5パターンの多数派の値と一致した割合")
    out(f"  {'項目':<14}{'パターン内':>18}{'パターン間':>16}")
    for s in scopes:
        w = sum(hits[s].values())
        b, n = between[s]
        out(f"  {s:<14}{frac(w, len(ok)):>18}{frac(b, n):>16}")

    # ---------- 4 ----------
    resp_path = ROOT / "results" / f"responses_{args.tag}.csv"
    out(f"\n■ 4. 揺れの分類の内訳（出典: results/{resp_path.name}。基準は項目ごとの全体の最頻値）")
    if not resp_path.exists():
        out(f"  {resp_path.name} がありません。先に aggregate.py --tag {args.tag} を実行してください。")
    else:
        with open(resp_path, "r", encoding="utf-8-sig", newline="") as f:
            resp = [r for r in csv.DictReader(f) if r["状態"] == "ok"]
        cats = [c for c, _ in Counter(r[f"{i}_分類"] for r in resp for i in items).most_common()]
        total = Counter(r[f"{i}_分類"] for r in resp for i in items)
        out("  全体: " + " / ".join(f"{c} {total[c]}" for c in cats) + f"  （全 {len(resp) * len(items)} 箇所）")
        out("  表の各マスは " + "／".join(cats) + " の件数")
        cell = lambda rows, its: "／".join(str(sum(r[f"{i}_分類"] == c for r in rows for i in its)) for c in cats)
        out(f"  {'項目':<14}" + "".join(f"{p:>12}" for p in patterns) + f"{'計':>14}")
        for i in items:
            out(f"  {i:<14}" + "".join(f"{cell([r for r in resp if r['パターン'] == p], [i]):>12}"
                                       for p in patterns) + f"{cell(resp, [i]):>14}")
        out(f"  {'計':<14}" + "".join(f"{cell([r for r in resp if r['パターン'] == p], items):>12}"
                                     for p in patterns) + f"{cell(resp, items):>14}")

    # ---------- 5 ----------
    if args.count_item and args.count_token:
        item, token = args.count_item, normalize(args.count_token)
        if item not in items:
            sys.exit(f"エラー: --count-item は {items} のどれかにしてください。")
        out(f"\n■ 5. 「{item}」の値に「{token}」が出てくる回数（正規化後）")
        counts = {p: Counter((r[item] or "").count(token) for r in norm[p]) for p in patterns}
        ks = sorted({k for c in counts.values() for k in c})
        out(f"  {'回数':<8}" + "".join(f"{p:>8}" for p in patterns) + f"{'計':>8}")
        for k in ks:
            out(f"  {str(k) + '回':<8}" + "".join(f"{counts[p][k]:>8}" for p in patterns)
                + f"{sum(counts[p][k] for p in patterns):>8}")
        out("\n  [回数ごとの値と試行（原文のまま）]")
        for k in ks:
            out(f"  {k}回")
            listing: dict[str, list[str]] = {}
            for p in patterns:
                for r in norm[p]:
                    if (r[item] or "").count(token) == k:
                        listing.setdefault(r["_raw"].get(item, "(欠落)"), []).append(f"{p}#{r['_trial']}")
            for value, where in sorted(listing.items(), key=lambda kv: -len(kv[1])):
                out(f"    {len(where):>2}件  {value}")
                out(f"          {' '.join(where)}")

    with open(ROOT / "results" / "variance_split.txt", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(LINES) + "\n")
    print("\n出力先: results\\variance_split.txt")


if __name__ == "__main__":
    main()
