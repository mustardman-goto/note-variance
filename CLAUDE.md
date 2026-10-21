# note-variance：プロンプトの揺れを測る検証ハーネス

同じ文書・同じタスクに対して「表現だけ変えたプロンプト」を複数回投げ、
出力の揺れ（同じプロンプトでのばらつき）と入力の揺れ（プロンプトを変えたときのばらつき）を
同じ尺度で比べる。毎週、対象文書とプロンプトを差し替えて回す。

## 再開の1コマンド

作業フォルダは、このリポジトリのルート。そこで PowerShell を開いて：

```powershell
.\.venv\Scripts\python.exe src\run.py; .\.venv\Scripts\python.exe src\aggregate.py
```

途中で落ちても同じコマンドで続きから再開する（成功済みの試行は飛ばす）。

`config/run.yaml` 以外の設定で動かすときは、どのスクリプトにも `--config` を付ける。
例（Sonnet の実行を再集計）：

```powershell
.\.venv\Scripts\python.exe src\aggregate.py --config config/run_2026w41-sonnet.yaml
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
- `src/extract_html.py` … 保存した HTML → 本文テキスト（範囲の指定は run.yaml の `material` にコメントで残す）
- `src/abstention_split.py` … 棄権の検証（2026-10-16_abstention〜）の集計。棄権率・でっち上げ率・誤棄権率・
  A/B/C/D・条件内／条件間 → `variance_split_<tag>.txt`、`classified_<tag>.csv`、`need_review_<tag>.csv`
- 設定の形式は2つ。従来の `prompts`（パターンごとに全文）と、`prompt.body`＋`conditions`（本文1つに
  条件ごとの差し込み文）。後者は `common.expand_conditions` が前者の形に読み替える
- `config/ground_truth_abstention_v1.yaml` … 2026-10-16_abstention の正答基準。`stated_in_source`、
  `abstention_words`（棄権語。値全体が一致したときだけ abstain_plain）、`abstention_reference_words`
  （参照先を示す語。含めば abstain_reference）、`forbidden_patterns`（正規表現）を持つ。
  確定した7つの判断はファイル冒頭のコメントにある
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
- ターミナルの操作は最小限にする。コマンドは省略せず全文を示し、
  何をするコマンドかを日本語で1行添える
- 記事の執筆はこのハーネスの範囲外（数字を出すところまで）

## 指標の定義（記事に書くときの根拠）

定義は実行ごとに違う。数字を使う前に、どの実行（run_id）の数字かを確かめること。

### 2026w41（2026w41-bousai-koboyoryo と -sonnet。2本目の記事）

以下は 2026w41 の2つの実行についての定義。設計は4項目・5パターン（P1～P5）×各10回。
集計は `aggregate.py`・`compare_criteria.py`・`variance_split.py`、出力は `results/` と `results/2026w41-sonnet/`。

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

### 2026-10-16_abstention（棄権指示の有無で、記載のない項目への答え方が変わるか）

以下は 2026-10-16_abstention の実行についての定義。設計は6項目（うち2項目は素材に記載なし）・
3条件（C0 棄権指示なし／C1「記載なし」と書くよう指示／C2 推測禁止を明示）×各20回。
集計は `abstention_split.py`、出力は `results/2026-10-16_abstention/`。正答基準は `config/ground_truth_abstention_v1.yaml`。
2026w41 のペア単位の一致率（上の「揺れ」）は、この実行では出していない。

- **フォーマット遵守**（応答単位）：空行を除いて6行、6行すべてが指定の項目名＋コロン、各値が1～40字
- **値の読み方の2つの版**
  - **v1**：結果を見る前に決めたルール。行頭に「1. 」などの番号を付けた行はラベルが指定と違うので、その項目は値なし
  - **v1_numbered**：本実行の結果を見たあとに追加したルール。行頭の「数字＋. 」だけを外して値を読む。
    形式の判定は v1 と同じ（番号付きは形式違反のまま）
- **記載なし項目**（`stated_in_source: false`）は上から順に1つに分ける：
  欠落（値が無い）／でっち上げ（`forbidden_patterns` に当たる）／棄権／unclassified（どれでもない）
  - 棄権＝abstain_plain（値全体が棄権語と一致）＋ abstain_reference（「参照」「手数料表」「別表」など参照先を示す語を含む）
- **記載あり項目**は上から順に A～D の1つに分ける：
  A 形式違反（値が無い・ラベル違い・字数外）／B 内容の誤り（required 未充足・forbidden を含む）／
  D 表記のゆれ（正規化後に基準値と一致）／C 情報量のゆれ（どれでもない）。基準値は全条件・全試行の正規化後の最頻値
  - 誤棄権（記載ありなのに棄権の形で答えた）は A～D とは別に数える
- **正規化**（`normalize_abcd`）：NFKC・空白を消す・括弧の種類・数字の桁区切り・列挙の区切り・波ダッシュ・文末句点をそろえる。
  2026w41 の「正規化後」（NFKC・空白・文末句点だけ）より範囲が広い
- **variance_split_<tag>.txt の尺度**（正規化後の値で、最頻値との一致を数える。値が無い試行は「(欠落)」という値として数える）
  - 条件内：各試行の値が、その条件の最頻値と一致した割合
  - 条件間：各条件の最頻値が、3条件の多数派の値と一致した割合（「最頻値ペアの一致」も併記）
  - 同数1位の最頻値がある箇所は※で出る。条件間の値はどれを最頻値に取るかで変わりうる
- 分類のルールの全文は `src/abstention_split.py` の冒頭、確定した7つの判断は正答基準ファイルの冒頭にある

## 既知の注意点

### すべての実行に共通

- モデルは起動時に `/v1/models` から `select_contains` を含む最新を選ぶ。週によって別のモデルになりうる
  （実際に使ったIDは1件ごとのログと manifest に残る）
- 現行モデルは「思考」が常に入り、出力トークンに思考分が含まれる。`effort` は null（モデルの既定）
- temperature は SDK 1.x の引数に無いため `extra_body` で送っている。受理はされるが、
  実際に効いているかは確認していない。既定値以外を送るとエラーになるモデルがある
- 安全分類による拒否時の代替モデルへの切り替え（fallbacks）は、測定が混ざるので使っていない
- `data/source/` の元資料（PDF・HTML）はコミットしない（再配布を避ける）。URL は設定ファイル、テキストは `data/` にある
- 検定はしていない。条件（パターン）単位の数ポイントの差は誤差の範囲かもしれない

### 2026w41 だけに当てはまるもの

- v2 の応募期間は「令和7年」がどこかに1回あれば満たされるので、終了日の年の欠落は検出できない
  （v1→v2 で判定が変わった箇所は Haiku・Sonnet とも 0/200）。件数は variance_split.py の
  `--count-token` で別に数えている
- v2 の optional「補助対象業務の内容」は「業務内容」という書き方に一致しない（Sonnet で 0/50）
- プロンプトに条件差が1つ混ざっている：P2 と P5 は項目を文中に並べ、P1・P3・P4 は箇条書き
- 各パターン10回
- pdfplumber は表の行順を崩すことがある。抽出テキストは手で直さず、そのまま使う
- 設定は `config/run.yaml` ではなく Git の履歴（c3fb341 の `config/run.yaml`）と `config/run_2026w41-sonnet.yaml` にある。
  いまの `config/run.yaml` は 2026-10-16_abstention の設定
- max_tokens は 16000

### 2026-10-16_abstention だけに当てはまるもの

- 各条件20回。素材は1ページ（国立公文書館の HTML から `extract_html.py` で本文を切り出したもの）、モデルは claude-sonnet-5-5 だけ
- 行頭に番号を付けた応答が60件中15件（C0 6／C1 3／C2 6）。v1 と v1_numbered で記載なし項目の数字が大きく変わる。
  v1_numbered は結果を見たあとに追加したルールなので、事前に決めたルールの結果は v1
- 思考は60件すべてで出ているが、API の既定では中身が返らず、生ログの `thinking` は60件とも0文字
- max_tokens は 2000（400 ではドライラン3件すべてが思考で上限に達し本文が切れたため変更）。2026w41（16000）と違う
- unclassified が残っている（v1 7件、v1_numbered 10件。すべて C0 の「1回の請求で申し込めるカット数の上限」）。
  人の判断はまだ入れていない。内容は `need_review_*.csv`
- 思考なし（between_tools）で試したドライラン1件が `results/raw/2026-10-16_abstention.dryrun.jsonl` に残っている。本実行の集計には入っていない

## 実行履歴

| run_id | 日付 | モデル | 条件 | 結果 |
|---|---|---|---|---|
| 2026w41-bousai-koboyoryo | 2026-10-09 13:21 | claude-haiku-5-5 | 5パターン×10回、temperature 1.0 | 50/50 成功。`results/` |
| 2026w41-bousai-koboyoryo-sonnet | 2026-10-09 15:46 | claude-sonnet-5-5 | 同上（プロンプト同一）。設定は `config/run_2026w41-sonnet.yaml` | 50/50 成功。`results/2026w41-sonnet/` |
| 2026-10-16_abstention | 2026-10-20 23:39 | claude-sonnet-5-5 | 3条件×20回、temperature 1.0。設定は `config/run.yaml` | 60/60 成功。`results/2026-10-16_abstention/` |
