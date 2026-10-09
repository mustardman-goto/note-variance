"""2つの正答基準で同じ生ログを判定し、違いを results/criteria_diff.txt に出す。

使い方:
    python src/compare_criteria.py config/ground_truth_v1.yaml config/ground_truth_v2.yaml

どちらの基準が正しいかは判断しない。判定が変わった箇所と正答率の変化を並べるだけ。
"""
import sys

from aggregate import failed_reasons, has_any, load_ground_truth, load_ok_records, pct
from common import ROOT, load_config, read_text, setup_stdout, sha256

LINES: list[str] = []


def out(line: str = "") -> None:
    print(line)
    LINES.append(line)


def mark(ok: bool) -> str:
    return "正" if ok else "誤"


def main() -> None:
    setup_stdout()
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    path_a, path_b = sys.argv[1], sys.argv[2]
    gt_a, gt_b = load_ground_truth(path_a), load_ground_truth(path_b)
    for path, gt in ((path_a, gt_a), (path_b, gt_b)):
        if gt is None:
            sys.exit(f"エラー: {path} が無いか、confirmed: true になっていません。")

    cfg = load_config()
    items: list[str] = cfg["task"]["items"]
    patterns = list(cfg["prompts"].keys())
    _, latest, ok = load_ok_records(cfg)
    rows = [ok[k] for k in sorted(ok)]

    out("=" * 72)
    out(f"正答基準の比較  run_id: {cfg['run_id']}")
    out("=" * 72)
    out(f"  A: {path_a}  sha256 {sha256(read_text(path_a))[:16]}")
    out(f"  B: {path_b}  sha256 {sha256(read_text(path_b))[:16]}")
    out(f"  対象: 成功した試行 {len(rows)} 件 × {len(items)} 項目 = {len(rows) * len(items)} 箇所"
        f"（失敗した試行 {len(latest) - len(ok)} 件は対象外）")
    out("  ※どちらの基準が正しいかの判断は含まない。")

    # ---- 基準そのものの違い ----
    out("\n■ 1. 基準の違い（required / forbidden / optional）")
    for i in items:
        ra, rb = gt_a["items"][i], gt_b["items"][i]
        out(f"  {i}")
        for key in ("required", "forbidden", "optional"):
            va, vb = ra.get(key, []), rb.get(key, [])
            if va == vb:
                out(f"    {key:9}: 同じ {va}")
            else:
                out(f"    {key:9}: A {va}")
                out(f"    {'':9}  B {vb}")
    out("  ※optional は aggregate.py では正誤に使わず、含有率を参考として出すだけ。")

    # ---- 判定 ----
    for r in rows:
        v = r["parsed"]["values"]
        r["a"] = {i: failed_reasons(v.get(i), gt_a["items"][i]) for i in items}
        r["b"] = {i: failed_reasons(v.get(i), gt_b["items"][i]) for i in items}

    out("\n■ 2. A→B で判定が変わった試行")
    changed = [(r, i) for r in rows for i in items if bool(r["a"][i]) != bool(r["b"][i])]
    if not changed:
        out("  なし（0 箇所）。全ての試行・全ての項目で、A と B の正誤判定は同じ。")
    for r, i in changed:
        out(f"  {r['pattern']} #{r['trial']:02d} 「{i}」 {mark(not r['a'][i])} → {mark(not r['b'][i])}")
        out(f"      値: {r['parsed']['values'].get(i)}")
        for reason in r["a"][i]:
            out(f"      A で誤の理由: {reason}")
        for reason in r["b"][i]:
            out(f"      B で誤の理由: {reason}")
    trials_changed = sorted({(r["pattern"], r["trial"]) for r, _ in changed})
    out(f"  判定が変わった箇所: {len(changed)} / {len(rows) * len(items)}、"
        f"試行数: {len(trials_changed)} / {len(rows)}")

    # ---- 正答率の変化 ----
    out("\n■ 3. 正答率の変化（A → B）")
    scopes = [(p, [r for r in rows if r["pattern"] == p]) for p in patterns] + [("全体", rows)]
    for label, key_items in [(i, [i]) for i in items] + [("4項目すべて", items)]:
        out(f"  {label}")
        for p, rs in scopes:
            na = sum(all(not r["a"][i] for i in key_items) for r in rs)
            nb = sum(all(not r["b"][i] for i in key_items) for r in rs)
            diff = f"{(nb - na) / len(rs) * 100:+.1f}pt" if rs else "-"
            out(f"    {p:4} {pct(na, len(rs)):>16} → {pct(nb, len(rs)):>16}  {diff}")

    # ---- B にだけある optional ----
    if any(gt_b["items"][i].get("optional") or gt_a["items"][i].get("optional") for i in items):
        out("\n■ 4. [参考] optional の語を含む出力の割合（正誤には使っていない）")
        for name, gt in (("A", gt_a), ("B", gt_b)):
            for i in items:
                for group in gt["items"][i].get("optional", []):
                    num = sum(has_any(r["parsed"]["values"].get(i), group) for r in rows)
                    out(f"    {name} / {i} / {group[0]} など: {pct(num, len(rows))}")

    out_path = ROOT / "results" / "criteria_diff.txt"
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(LINES) + "\n")
    print(f"\n出力先: {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
