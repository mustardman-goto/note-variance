"""プロンプトの揺れ検証：実行スクリプト。

使い方:
    python src/run.py                          # run.yaml のとおり本実行（途中からなら自動で再開）
    python src/run.py --dry-run --patterns P1 --trials 2   # お試し。別ファイルに保存される

結果は results/raw/<run_id>.jsonl に 1レスポンス＝1行で追記される。
"""
import argparse
import json
import random
import sys
import time
from datetime import datetime

import anthropic

from common import (
    ROOT, append_jsonl, cost_usd, load_api_key, load_config, price_for,
    raw_path, read_jsonl, read_text, setup_stdout, sha256,
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def choose_model(client: anthropic.Anthropic, model_cfg: dict) -> tuple[str, list[str]]:
    """/v1/models の一覧から、設定の条件に合うモデルIDを選ぶ。"""
    models = list(client.models.list())
    ids = [m.id for m in models]
    pin = model_cfg.get("pin")
    if pin:
        if pin not in ids:
            sys.exit(f"エラー: pin に指定した {pin} は /v1/models の一覧にありません。\n一覧: {ids}")
        return pin, ids
    key = model_cfg["select_contains"]
    candidates = [m for m in models if key in m.id]
    if not candidates:
        sys.exit(f"エラー: ID に「{key}」を含むモデルが一覧にありません。\n一覧: {ids}")
    newest = max(candidates, key=lambda m: m.created_at)
    return newest.id, ids


def call_once(client: anthropic.Anthropic, model_id: str, model_cfg: dict, prompt: str):
    kwargs = {
        "model": model_id,
        "max_tokens": model_cfg["max_tokens"],
        "messages": [{"role": "user", "content": prompt}],
    }
    if model_cfg.get("effort"):
        kwargs["output_config"] = {"effort": model_cfg["effort"]}
    if model_cfg.get("temperature") is not None:
        # SDK 1.x では temperature 引数が無いため、リクエスト本文に直接入れる
        kwargs["extra_body"] = {"temperature": model_cfg["temperature"]}
    return client.messages.create(**kwargs)


def run_trial(client, model_id: str, cfg: dict, prompt: str) -> dict:
    """1試行。リトライ対象のエラーは指数バックオフで再試行し、経過をすべて記録する。"""
    retry_cfg = cfg["retry"]
    retries = []
    result = {"status": "error", "error": None}
    started = time.monotonic()

    for attempt in range(1, retry_cfg["max_attempts"] + 1):
        retry_after = None
        try:
            msg = call_once(client, model_id, cfg["model"], prompt)
            usage = msg.usage
            result = {
                "status": "ok",
                "error": None,
                "text": "".join(b.text for b in msg.content if b.type == "text"),
                "block_types": [b.type for b in msg.content],
                "stop_reason": msg.stop_reason,
                "model_returned": msg.model,
                "request_id": msg._request_id,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
            }
            break
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as e:
            # キーの問題は続けても全部失敗するので、記録して止める
            result = {"status": "error", "fatal": True,
                      "error": {"type": type(e).__name__, "status": e.status_code, "message": e.message}}
            break
        except anthropic.RateLimitError as e:
            err = {"type": type(e).__name__, "status": e.status_code, "message": e.message}
            ra = e.response.headers.get("retry-after")
            retry_after = float(ra) if ra and ra.replace(".", "", 1).isdigit() else None
        except anthropic.APIStatusError as e:
            err = {"type": type(e).__name__, "status": e.status_code, "message": e.message}
            if e.status_code < 500:
                # 400 などはリトライしても直らないので、失敗として記録する
                result = {"status": "error", "error": err}
                break
        except anthropic.APIConnectionError as e:
            err = {"type": type(e).__name__, "status": None, "message": str(e)}

        result = {"status": "error", "error": err}
        if attempt == retry_cfg["max_attempts"]:
            break
        delay = min(retry_cfg["base_delay_sec"] * 2 ** (attempt - 1) + random.uniform(0, 1),
                    retry_cfg["max_delay_sec"])
        if retry_after:
            delay = max(delay, retry_after)
        retries.append({"attempt": attempt, "error": err, "wait_sec": round(delay, 1)})
        print(f"    リトライ {attempt}/{retry_cfg['max_attempts']}: {err['type']} → {delay:.1f}秒待機")
        time.sleep(delay)

    result["api_calls"] = len(retries) + 1
    result["retries"] = retries
    result["latency_sec"] = round(time.monotonic() - started, 2)
    return result


def main() -> None:
    setup_stdout()
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/run.yaml", help="設定ファイル（省略時は config/run.yaml）")
    ap.add_argument("--dry-run", action="store_true", help="お試し実行（別ファイルに保存）")
    ap.add_argument("--patterns", nargs="*", help="実行するパターン（省略時は全部）")
    ap.add_argument("--trials", type=int, help="1パターンあたりの回数（省略時は run.yaml の値）")
    args = ap.parse_args()
    if (args.patterns or args.trials) and not args.dry_run:
        sys.exit("エラー: --patterns / --trials は --dry-run と一緒に使ってください。本実行の条件は run.yaml で決めます。")

    cfg = load_config(args.config)
    config_sha = sha256(read_text(args.config))
    document = read_text(cfg["document"]["path"])
    document_sha = sha256(document)
    patterns = args.patterns or list(cfg["prompts"].keys())
    trials = args.trials or cfg["trials_per_pattern"]
    out_path = raw_path(cfg, args.dry_run)

    client = anthropic.Anthropic(api_key=load_api_key(), max_retries=0)  # リトライは自前で記録する

    # --- モデルの決定。本実行の再開時は、最初に選んだモデルを使い続ける ---
    manifest_path = out_path.with_suffix(".manifest.json")
    if not args.dry_run and manifest_path.exists():
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        if manifest["config_sha256"] != config_sha or manifest["document_sha256"] != document_sha:
            sys.exit("エラー: 前回の途中から設定ファイルか対象文書が変わっています。条件が混ざるので止めました。\n"
                     "新しい検証なら run.yaml の run_id を変えてください。")
        model_id = manifest["model_id"]
        print(f"再開: モデルは前回と同じ {model_id}")
    else:
        model_id, all_ids = choose_model(client, cfg["model"])
        print(f"/v1/models から取得した {len(all_ids)} 件のうち {model_id} を選択")
        if not args.dry_run:
            manifest = {"run_id": cfg["run_id"], "config_path": args.config, "model_id": model_id, "models_listed": all_ids,
                        "config_sha256": config_sha, "document_sha256": document_sha,
                        "started_at": now_iso()}
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)

    # --- すでに成功している試行は飛ばす（再開） ---
    done = {(r["pattern"], r["trial"]) for r in read_jsonl(out_path)
            if r["status"] == "ok" and not args.dry_run}
    todo = [(p, t) for p in patterns for t in range(1, trials + 1) if (p, t) not in done]
    print(f"保存先: {out_path.relative_to(ROOT)}")
    print(f"予定 {len(patterns) * trials} 件 / 済み {len(done)} 件 / これから {len(todo)} 件")
    print(f"temperature: {cfg['model'].get('temperature')}  effort: {cfg['model'].get('effort')}  "
          f"max_tokens: {cfg['model']['max_tokens']}\n")

    price = price_for(cfg, model_id)
    session = {"ok": 0, "error": 0, "in": 0, "out": 0, "sec": 0.0}
    consecutive_errors = 0
    wall_start = time.monotonic()

    for pattern, trial in todo:
        prompt = cfg["prompts"][pattern]["template"].replace("{document}", document)
        ts_start = now_iso()
        result = run_trial(client, model_id, cfg, prompt)
        record = {
            "run_id": cfg["run_id"], "dry_run": args.dry_run,
            "pattern": pattern, "trial": trial,
            "ts_start": ts_start, "ts_end": now_iso(),
            "model_requested": model_id,
            "temperature": cfg["model"].get("temperature"),
            "effort": cfg["model"].get("effort"),
            "max_tokens": cfg["model"]["max_tokens"],
            "prompt_sha256": sha256(prompt), "document_sha256": document_sha,
            "config_sha256": config_sha,
            **result,
        }
        append_jsonl(out_path, record)

        session["sec"] += result["latency_sec"]
        if result["status"] == "ok":
            consecutive_errors = 0
            session["ok"] += 1
            session["in"] += result["input_tokens"]
            session["out"] += result["output_tokens"]
            print(f"  {pattern} #{trial:02d} ok  入力{result['input_tokens']} 出力{result['output_tokens']} "
                  f"{result['latency_sec']}秒  stop={result['stop_reason']}")
        else:
            consecutive_errors += 1
            session["error"] += 1
            e = result["error"]
            print(f"  {pattern} #{trial:02d} 失敗  {e['type']} (HTTP {e['status']}): {e['message']}")
            if result.get("fatal"):
                sys.exit("APIキーが拒否されました。.env のキーを確認してください（記録は残しました）。")
            if consecutive_errors >= 3:
                print("\n3件連続で失敗したので中断しました（記録は残しました）。原因を直してから同じコマンドで再開できます。")
                break

    wall = time.monotonic() - wall_start
    n = session["ok"] + session["error"]
    print(f"\n--- 今回の実行 ---")
    print(f"成功 {session['ok']} 件 / 失敗 {session['error']} 件 / 経過 {wall:.1f}秒")
    if session["ok"]:
        c = cost_usd(price, session["in"], session["out"])
        print(f"入力トークン合計 {session['in']}（1件平均 {session['in'] / session['ok']:.0f}）")
        print(f"出力トークン合計 {session['out']}（1件平均 {session['out'] / session['ok']:.0f}、思考分を含む）")
        print(f"1件平均 {session['sec'] / n:.1f}秒")
        print(f"コスト: {'$%.4f' % c if c is not None else '不明（run.yaml に単価なし）'}"
              + (f"（1件平均 ${c / session['ok']:.4f}）" if c is not None else ""))


if __name__ == "__main__":
    main()
