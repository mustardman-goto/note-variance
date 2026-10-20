"""run.py と aggregate.py の共通処理（設定読み込み・ファイル入出力）。"""
import hashlib
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def setup_stdout() -> None:
    """Windows でも日本語が化けないよう、標準出力を UTF-8 にする。"""
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def load_config(path: str = "config/run.yaml") -> dict:
    with open(ROOT / path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return expand_conditions(cfg) if "conditions" in cfg else cfg


def expand_conditions(cfg: dict) -> dict:
    """「1つの本文＋条件ごとの差し込み文」形式の設定（2026-10-16_abstention から）を、
    run.py / aggregate.py が使う従来の形（prompts・task・document など）に読み替える。
    元のキーは残す。従来形式の設定ファイルはこの関数を通らない。"""
    body = cfg["prompt"]["body"]
    cfg["document"] = {"path": cfg["material"]["local_path"], "source_url": cfg["material"]["url"]}
    cfg["task"] = {"items": cfg["items"], "max_value_chars": cfg["max_value_chars"]}
    # {abstention} を条件ごとの文に置き換える。{material} は従来の {document} として run.py が差し込む
    cfg["prompts"] = {
        c["id"]: {"note": c["label"],
                  "template": body.replace("{abstention}", c["abstention"]).replace("{material}", "{document}")}
        for c in cfg["conditions"]
    }
    cfg["trials_per_pattern"] = cfg["trials_per_condition"]
    m = cfg["model"]
    cfg["model"] = {"pin": m.get("id"), "select_contains": m["family"], "max_tokens": cfg["max_tokens"],
                    "temperature": cfg["temperature"], "effort": m.get("effort"), "thinking": m.get("thinking")}
    return cfg


def load_api_key() -> str:
    """.env から ANTHROPIC_API_KEY を読む。値は絶対に表示しない。"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        sys.exit("エラー: .env がありません。ANTHROPIC_API_KEY=... の1行を書いて置いてください。")
    with open(env_path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    return key
    sys.exit("エラー: .env に ANTHROPIC_API_KEY の値がありません。")


def read_text(rel_path: str) -> str:
    with open(ROOT / rel_path, "r", encoding="utf-8") as f:
        return f.read()


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def results_dir(cfg: dict) -> Path:
    """集計結果の出力先。run.yaml に output_dir があればそこ、無ければ results/。"""
    return ROOT / (cfg.get("output_dir") or "results")


def raw_path(cfg: dict, dry_run: bool) -> Path:
    suffix = ".dryrun.jsonl" if dry_run else ".jsonl"
    return ROOT / "results" / "raw" / (cfg["run_id"] + suffix)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def append_jsonl(path: Path, record: dict) -> None:
    """1レスポンス＝1行で追記する。途中で落ちてもここまでの分は残る。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def price_for(cfg: dict, model_id: str) -> dict | None:
    for prefix, price in (cfg.get("pricing_usd_per_mtok") or {}).items():
        if model_id.startswith(prefix):
            return price
    return None


def cost_usd(price: dict | None, input_tokens: int, output_tokens: int) -> float | None:
    if price is None:
        return None
    return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000
