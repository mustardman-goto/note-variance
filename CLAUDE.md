# note-variance：プロンプトの揺れを測る検証ハーネス

同じ文書・同じタスクに対して「表現だけ変えたプロンプト」を複数回投げ、
出力の揺れ（同じプロンプトでのばらつき）と入力の揺れ（プロンプトを変えたときのばらつき）を
同じ尺度で比べる。毎週、対象文書とプロンプトを差し替えて回す。

## 再開の1コマンド

作業フォルダは `C:\Users\fende\fde-practice\note-variance`。PowerShell で：

```powershell
cd $HOME\fde-practice\note-variance; .\.venv\Scripts\python.exe src\run.py; .\.venv\Scripts\python.exe src\aggregate.py
```

途中で落ちても同じコマンドで続きから再開する（成功済みの試行は飛ばす）。

## 次週分を回す手順

1. 対象が PDF なら `data/source/` に置いてテキスト化する
   `.\.venv\Scripts\python.exe src\extract_pdf.py data\source\<名前>.pdf data\<名前>.txt`
2. `config/run.yaml` を書き換える：`run_id`（必ず変える）、`document`、`task.items`、`prompts`
3. `config/ground_truth.yaml` を書き換え、**結果を見る前に**ユーザーと正解を決めて `confirmed: true` にする
4. ドライラン：`.\.venv\Scripts\python.exe src\run.py --dry-run --patterns P1 --trials 2`
   → コストと所要時間をユーザーに報告し、OK をもらってから本実行
5. 本実行と集計（上の1コマンド）
6. `git status` で `.env` が出ていないことを確認してからコミット

`.venv` が無い環境では先に：`python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt`

## 構成

- `config/run.yaml` … 設定の全部（文書、プロンプト、回数、モデルの選び方、temperature、単価）
- `config/ground_truth.yaml` … 正答判定の基準
- `src/run.py` … 実行。`results/raw/<run_id>.jsonl` に1レスポンス1行で追記
- `src/aggregate.py` … 集計。`results/` に summary.csv / responses.csv / variants.csv / report.txt
- `src/extract_pdf.py` … PDF → テキスト
- `results/raw/<run_id>.manifest.json` … 本実行の開始時に選んだモデルIDと設定のハッシュ

## 守ること

- **リポジトリは Public。** API キーは `.env` のみ。表示しない、コードに書かない、`.env` を読んで中身を出さない。
  もしコミットしてしまったら、取り消しより先に Anthropic Console でキーを失効させる
- 文字コードは全部 UTF-8。PowerShell の `>` でテキストを書き出さない。
  Python は `open(..., encoding="utf-8")` を明示、CSV は `utf-8-sig`
- 数字を作らない。失敗した試行は失敗として `raw/*.jsonl` に残す。`raw/` の記録は手で編集しない
- 設定の変更は `run.yaml` で行い、コードは触らない。本実行の途中で `run.yaml` を変えると
  条件が混ざるため `run.py` が止まる（新しい検証は `run_id` を変える）
- ユーザーはターミナルと Git に不慣れ。コマンドは1つずつ、何をするかの説明を添える
- 記事の執筆はこのハーネスの範囲外（数字を出すところまで）

## 指標の定義（記事に書くときの根拠）

- **フォーマット遵守**：空行を除いて4行、4行すべてが指定の項目名＋コロン、各値が1～50字
- **揺れ（2と3で共通の尺度）**：出力を2つ取り出したとき値が一致するペアの割合。
  同一パターン内のペア＝出力の揺れ、異なるパターン間のペア＝入力の揺れ。
  「原文」は一字一句、「正規化後」は NFKC（全角/半角）・空白・文末句点の違いを無視
- **ラベル違反の扱い**：項目名だけ書き換えられた行（指定外ラベル1行・欠けた項目1つ）は、
  遵守率では違反に数え、値の比較と正誤にはその行の値を使う
- **正答**：`ground_truth.yaml` の required の各グループから1語以上を含み、forbidden を含まない
- **揺れの分類**：基準は項目ごとの最頻値。一致 / 表記のゆれ（正規化で一致）/
  情報量のゆれ（正規化しても違うが正答。言い換えも含む粗い分類）/ 内容の誤り（正答条件を満たさない）

## 既知の注意点

- モデルは起動時に `/v1/models` から `select_contains` を含む最新を選ぶ。週によって別のモデルになりうる
  （実際に使ったIDは1件ごとのログと manifest に残る）
- 現行モデルは「思考」が常に入り、出力トークンに思考分が含まれる。`effort` は null（モデルの既定）
- temperature は SDK 1.x の引数に無いため `extra_body` で送っている。受理はされるが、
  実際に効いているかは確認していない。既定値以外を送るとエラーになるモデルがある
- 安全分類による拒否時の代替モデルへの切り替え（fallbacks）は、測定が混ざるので使っていない
- pdfplumber は表の行順を崩すことがある。抽出テキストは手で直さず、そのまま使う
- `data/source/` の PDF はコミットしない（再配布を避ける）。URL は `run.yaml`、テキストは `data/` にある

## 実行履歴

| run_id | 日付 | モデル | 条件 | 結果 |
|---|---|---|---|---|
| 2026w41-bousai-koboyoryo | 2026-10-09 | claude-haiku-5-5 | 5パターン×10回、temperature 1.0 | 50/50 成功。`results/report.txt` |
