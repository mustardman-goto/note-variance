"""PDF をテキスト化して data/ に保存する（週に1回、対象文書を差し替えたときだけ実行）。

使い方:
    python src/extract_pdf.py data/source/xxx.pdf data/xxx.txt
"""
import hashlib
import sys
from pathlib import Path

import pdfplumber


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    pdf_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])

    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append((page.extract_text() or "").strip())
    text = "\n\n".join(pages) + "\n"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    empty = [i + 1 for i, p in enumerate(pages) if not p]
    print(f"ページ数: {len(pages)}")
    print(f"文字数: {len(text)}（空白・改行を除くと {len(''.join(text.split()))}）")
    print(f"テキストが取れなかったページ: {empty if empty else 'なし'}")
    print(f"sha256: {sha}")
    print(f"保存先: {out_path}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
