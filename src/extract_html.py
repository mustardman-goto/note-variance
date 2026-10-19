"""保存済みの HTML から本文だけをテキスト化して data/ に保存する（週に1回、対象文書を差し替えたときだけ実行）。

使い方:
    python src/extract_html.py data/source/xxx/page.html data/xxx.txt --start-class copy --stop-id FooterLayout --skip-class ikari

本文の範囲:
    --start-class で指定した class を持つ要素の中から始め、--stop-id の要素が始まったところで終わる。
    --skip-class の要素（ページ内リンクの目次など）は中身ごと飛ばす。
    script / style / 画像は出力しない。表は1行ごとに、セルを「 | 」でつないで出す。
    HTML の文字コードは <meta charset> から読む（無ければ --encoding）。
"""
import argparse
import hashlib
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr", "dt", "dd"}
CELL_TAGS = {"td", "th"}
VOID_TAGS = {"br", "img", "hr", "meta", "link", "input"}
DROP_TAGS = {"script", "style"}


class BodyExtractor(HTMLParser):
    def __init__(self, start_class: str, stop_id: str, skip_class: str | None):
        super().__init__(convert_charrefs=True)
        self.start_class, self.stop_id, self.skip_class = start_class, stop_id, skip_class
        self.inside = False
        self.done = False
        self.skip_depth = 0      # 飛ばしている要素の入れ子の深さ（0 なら飛ばしていない）
        self.drop_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if self.inside and a.get("id") == self.stop_id:
            self.inside, self.done = False, True
            return
        if not self.inside:
            if self.start_class in classes:
                self.inside = True
            return
        if self.skip_depth:
            if tag not in VOID_TAGS:
                self.skip_depth += 1
            return
        if self.skip_class and self.skip_class in classes:
            self.skip_depth = 1
            return
        if tag in DROP_TAGS:
            self.drop_depth += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in CELL_TAGS:
            self.parts.append(" | ")

    def handle_endtag(self, tag):
        if not self.inside:
            return
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if tag in DROP_TAGS:
            self.drop_depth = max(0, self.drop_depth - 1)
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.inside and not self.skip_depth and not self.drop_depth:
            self.parts.append(re.sub(r"\s+", " ", data))


def tidy(raw: str) -> str:
    lines = []
    for ln in raw.split("\n"):
        ln = re.sub(r"[ \t]+", " ", ln).strip()
        ln = re.sub(r"^\|\s*", "", ln)      # 行頭のセル区切りを消す
        if ln:
            lines.append(ln)
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("html")
    ap.add_argument("out")
    ap.add_argument("--start-class", required=True)
    ap.add_argument("--stop-id", required=True)
    ap.add_argument("--skip-class")
    ap.add_argument("--encoding", default="utf-8", help="<meta charset> が無いときの文字コード")
    args = ap.parse_args()

    data = Path(args.html).read_bytes()
    m = re.search(rb"charset=[\"']?([\w-]+)", data[:4000])
    enc = m.group(1).decode("ascii") if m else args.encoding
    if enc.lower().replace("-", "_") in {"shift_jis", "sjis", "x_sjis"}:
        enc = "cp932"   # Shift_JIS と書かれたページの多くは Windows の拡張文字を含む
    html = data.decode(enc)  # 読めない文字があれば止まる（置き換えて黙って進めない）

    p = BodyExtractor(args.start_class, args.stop_id, args.skip_class)
    p.feed(html)
    if not p.done:
        sys.exit(f"エラー: 本文の終わり（id={args.stop_id}）が見つかりませんでした。")
    text = tidy("".join(p.parts))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    print(f"HTML の文字コード: {enc}")
    print(f"HTML の sha256: {hashlib.sha256(data).hexdigest()}")
    print(f"行数: {text.count(chr(10))}")
    print(f"文字数: {len(text)}（空白・改行を除くと {len(''.join(text.split()))}）")
    print(f"sha256: {hashlib.sha256(text.encode('utf-8')).hexdigest()}")
    print(f"保存先: {out_path}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
