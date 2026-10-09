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

`config/run.yaml` 以外の設定で動かすときは、どのスクリプトにも `--config` を付ける。
例（Sonnet の実行を再集計）：

```powershell
cd $HOME\fde-practice\note-variance; .\.venv\Scripts\python.exe src\aggregate.py --config config/run_2026w41-sonnet.yaml
```

## 次週分を回す手順

1. 対象が PDF なら `data/source/` に置いてテキスト化する
   `.\.venv\Scripts\python.exe src\extract_pdf.py data\source\<名前>.pdf data\<名前>.txt`
2. `config/run.yaml` を書き換える：`run_id`（必ず変える）、`document`、`task.items`、`prompts`
3. 正答基準 `config/ground_truth_<版>.yaml` を作り、**結果を見る前に**ユーザーと正解を決めて
   `confirmed: true` にする。`run.yaml` の `ground_truth` をそのファイルに向ける
4. **本実行の前に、設定・正答基準・予想（prediction.md）をコミットする。**
   「事前に決めた」と言えるのは、コミット時刻が実行時刻より前のときだけ
   （2026w41 の Haiku 実行では基準のコミットが実行の4分後になり、事前登録とは書けなくなった）
5. ドライラン：`.\.venv\Scripts\python.exe src\run.py --dry-run --patterns P1 --trials 2`
   → コストと所要時間をユーザーに報告し、OK をもらってから本実行
6. 本実行と集計（上の1コマンド）。続けて必要なら下の「追加の集計」
7. `git status` で `.env` が出ていないことを確認してからコミット

同じ週にモデルだけ変えて比べるときは、`run.yaml` を書き換えずに複製して
`config/run_<名前>.yaml` を作り、`run_id`・`output_dir`・`model.select_contains` だけ変えて
`--config` で実行する（プロンプトが同一かは生ログの `prompt_sha256` で確認できる）。

## 追加の集計（どれも API は呼ばない）

```powershell
# 正答基準を指定して集計。出力名の末尾は基準ファイル名から決まる（ground_truth_v1.yaml → summary_v1.csv）
.\.venv\Scripts\python.exe src\aggregate.py --ground-truth config/ground_truth_v1.yaml

# 2つの基準の判定を比べる → criteria_diff.txt（基準の違い、判定が変わった試行、正答率の変化）
.\.venv\Scripts\python.exe src\compare_criteria.py config/ground_truth_v1.yaml config/ground_truth_v2.yaml

# 揺れをパターン内／パターン間に分ける → variance_split.txt（数字のみ）
#   --tag         揺れの分類に使う responses_<tag>.csv（先に aggregate.py でその版を出しておく）
#   --count-item / --count-token   指定した項目の値に、その語が何回出てくるかをパターン別に数える
.\.venv\Scripts\python.exe src\variance_split.py --tag v2 --count-item 応募期間 --count-token 令和7年
```

どれも `--config` を付ければ別の実行が対象になり、出力先はその設定の `output_dir` になる。

`.venv` が無い環境では先に：`python -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt`

## 構成

- `config/run.yaml` … 設定の全部（文書、プロンプト、回数、モデルの選び方、temperature、単価、
  正答基準、出力先 `output_dir`）。既定の設定ファイル
- `config/run_<名前>.yaml` … 別条件の実行用（`--config` で指定）
- `config/ground_truth_v1.yaml` / `ground_truth_v2.yaml` … 正答判定の基準（版ごとに別ファイル。上書きしない）
- `src/run.py` … 実行。`results/raw/<run_id>.jsonl` に1レスポンス1行で追記
- `src/aggregate.py` … 集計。summary / responses / variants（CSV）と report（txt）を出す
- `src/compare_criteria.py` … 2つの正答基準の判定を比べる
- `src/variance_split.py` … 揺れをパターン内／パターン間に分ける
- `src/extract_pdf.py` … PDF → テキスト
- `results/raw/<run_id>.manifest.json` … 本実行の開始時に選んだモデルIDと設定のハッシュ
- `results/`（Haiku）、`results/2026w41-sonnet/`（Sonnet）… 集計結果。出力先は設定の `output_dir`
- `prediction.md` … 実行前に書いた予想

### 正答基準の版

- **v1**：最初のコミット（cca8240）時点の版。応募期間は年を必須としない
- **v2**：応募期間に「令和7年」などの年を必須とし、`optional` を持つ版。`run.yaml` はこちらを指す
- `optional` は正誤に使わず、その語を含む出力の割合を参考として出すだけ（この扱いは仮決め）
- `results/summary.csv` など末尾に版が付かないファイルは、最初のコミット時の v1 による結果。
  基準ファイル名が `ground_truth_<版>.yaml` の形であれば `aggregate.py` は版付きの名前で出すので、
  これらは上書きされない（この形でない名前の基準ファイルを使うと、末尾なしの名前で出て上書きされる）
- どちらの版が正しいかは決めていない。両方の結果を並べて残す

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
- **正答**：正答基準の required の各グループから1語以上を含み、forbidden を含まない
- **揺れの分類**：基準は項目ごとの最頻値。一致 / 表記のゆれ（正規化で一致）/
  情報量のゆれ（正規化しても違うが正答。言い換えも含む粗い分類）/ 内容の誤り（正答条件を満たさない）
- **variance_split.txt の尺度**（正規化後の値で、最頻値との一致を数える）
  - パターン内：各出力が、自分のパターンの最頻値と一致した割合
  - パターン間：各パターンの最頻値が、5パターンの多数派の値と一致した割合（「最頻値ペアの一致」も併記）
  - 同数1位の最頻値があるパターンでは、パターン間の値はどれを最頻値に取るかで変わりうる（ファイルに※で出る）

## 既知の注意点

- モデルは起動時に `/v1/models` から `select_contains` を含む最新を選ぶ。週によって別のモデルになりうる
  （実際に使ったIDは1件ごとのログと manifest に残る）
- 現行モデルは「思考」が常に入り、出力トークンに思考分が含まれる。`effort` は null（モデルの既定）
- temperature は SDK 1.x の引数に無いため `extra_body` で送っている。受理はされるが、
  実際に効いているかは確認していない。既定値以外を送るとエラーになるモデルがある
- 安全分類による拒否時の代替モデルへの切り替え（fallbacks）は、測定が混ざるので使っていない
- v2 の応募期間は「令和7年」がどこかに1回あれば満たされるので、終了日の年の欠落は検出できない
  （v1→v2 で判定が変わった箇所は Haiku・Sonnet とも 0/200）。件数は variance_split.py の
  `--count-token` で別に数えている
- v2 の optional「補助対象業務の内容」は「業務内容」という書き方に一致しない（Sonnet で 0/50）
- プロンプトに条件差が1つ混ざっている：P2 と P5 は項目を文中に並べ、P1・P3・P4 は箇条書き
- 各パターン10回。パターン単位の数ポイントの差は誤差の範囲かもしれない（検定はしていない）
- pdfplumber は表の行順を崩すことがある。抽出テキストは手で直さず、そのまま使う
- `data/source/` の PDF はコミットしない（再配布を避ける）。URL は `run.yaml`、テキストは `data/` にある

## 実行履歴

| run_id | 日付 | モデル | 条件 | 結果 |
|---|---|---|---|---|
| 2026w41-bousai-koboyoryo | 2026-10-09 13:21 | claude-haiku-5-5 | 5パターン×10回、temperature 1.0 | 50/50 成功。`results/` |
| 2026w41-bousai-koboyoryo-sonnet | 2026-10-09 15:46 | claude-sonnet-5-5 | 同上（プロンプト同一）。設定は `config/run_2026w41-sonnet.yaml` | 50/50 成功。`results/2026w41-sonnet/` |
