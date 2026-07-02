"""Monolithic (llama-cli) baseline runner."""

from __future__ import annotations

import hashlib
import os
import platform
import re
import subprocess
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LLAMA_BUILD = ROOT / "llama.cpp" / "build" / "bin"


def default_models_dir() -> Path:
    env = os.environ.get("MODELS_DIR", "")
    if env:
        return Path(env)
    home = Path(os.environ.get("USERPROFILE", os.environ.get("HOME", "")))
    return home / ".distributed-llm" / "models"


def find_llama_cli() -> Path | None:
    for key in ("LLAMA_CLI", "BENCHMARK_LLAMA_CLI"):
        if os.environ.get(key):
            p = Path(os.environ[key])
            if p.is_file():
                return p
    exe = "llama-cli.exe" if platform.system() == "Windows" else "llama-cli"
    for candidate in (LLAMA_BUILD / exe, LLAMA_BUILD / "llama-cli", Path(exe)):
        if candidate.is_file():
            return candidate
    return None


def find_gguf(model_id: str, filename: str, models_dir: Path | None = None) -> Path | None:
    root = models_dir or default_models_dir()
    model_dir = root / model_id
    if not model_dir.is_dir():
        return None
    # Prefer exact filename, then any gguf in tree
    exact = model_dir / filename
    if exact.is_file():
        return exact
    for path in model_dir.rglob("*.gguf"):
        if path.is_file() and path.stat().st_size > 1024 * 1024:
            return path
    return None


def run_llama_cli(
    gguf: Path,
    prompt: str,
    max_tokens: int,
    n_ctx: int = 512,
    ngl: int = 99,
    timeout_s: int = 300,
) -> dict[str, Any]:
    cli = find_llama_cli()
    if not cli:
        return {"ok": False, "error": "llama-cli not found", "measurement_source": "unavailable"}

    args = [
        str(cli),
        "-m", str(gguf),
        "-p", prompt,
        "-n", str(max_tokens),
        "-c", str(n_ctx),
        "-ngl", str(ngl),
        "--no-display-prompt",
        "--simple-io",
        "-s", "42",
    ]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(gguf.parent),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "timeout", "duration_ms": (time.perf_counter() - t0) * 1000}
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    text = proc.stdout.strip()
    tokens_est = max(len(text.split()), 1) if max_tokens > 1 else 1
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "duration_ms": round(elapsed_ms, 2),
        "text": text,
        "token_count_est": tokens_est,
        "tokens_per_sec": round(tokens_est / (elapsed_ms / 1000.0), 2) if elapsed_ms > 0 else 0,
        "stderr_tail": proc.stderr[-500:] if proc.stderr else "",
        "cli_path": str(cli),
        "measurement_source": "direct",
    }


def run_monolithic_repeat(
    model_cfg: dict[str, Any],
    prompt: str,
    max_tokens: int,
    n_ctx: int,
    ngl: int,
    timeout_s: int,
) -> dict[str, Any]:
    gguf = find_gguf(model_cfg["model_id"], model_cfg.get("filename", ""))
    if not gguf:
        return {
            "ok": False,
            "error": f"GGUF not found for {model_cfg['model_id']}",
            "models_dir": str(default_models_dir()),
        }

    load_t0 = time.perf_counter()
    # TTFT probe: 1 token
    ttft_run = run_llama_cli(gguf, prompt, 1, n_ctx, ngl, timeout_s)
    ttft_ms = ttft_run.get("duration_ms", 0)

    # Warmup
    warmup = run_llama_cli(gguf, prompt, 1, n_ctx, ngl, timeout_s)

    # Decode run
    decode = run_llama_cli(gguf, prompt, max_tokens, n_ctx, ngl, timeout_s)
    load_ms = (time.perf_counter() - load_t0) * 1000.0

    text = decode.get("text", "")
    first_token = _first_token(text)
    return {
        "backend": "monolithic",
        "gguf_path": str(gguf),
        "load": {
            "total_ms": round(load_ms, 2),
            "measurement_source": "direct",
            "note": "includes TTFT probe + warmup + decode subprocess runs",
        },
        "ttft": {
            "total_ms": ttft_ms,
            "measurement_source": "direct",
            "components": {
                "model_load_ms": None,
                "prefill_ms": ttft_ms,
                "first_token_ms": ttft_ms,
            },
        },
        "prefill": {
            "prompt_chars": len(prompt),
            "duration_ms": ttft_ms,
            "tokens_per_sec": round(1 / (ttft_ms / 1000.0), 2) if ttft_ms else None,
            "measurement_source": "direct",
        },
        "decode": {
            "duration_ms": decode.get("duration_ms"),
            "token_count": max_tokens,
            "tokens_per_sec": decode.get("tokens_per_sec"),
            "ms_per_token": round(decode.get("duration_ms", 0) / max(max_tokens, 1), 2),
            "measurement_source": "direct",
        },
        "warmup": warmup,
        "quality": _quality(text, first_token, prompt),
        "raw": {"ttft_run": ttft_run, "decode_run": decode},
    }


def _first_token(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    parts = re.split(r"(\s+)", text, maxsplit=1)
    return parts[0] if parts else text[:32]


def _quality(text: str, first_token: str, prompt: str) -> dict[str, Any]:
    h = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    return {
        "first_token": first_token,
        "output_hash": h,
        "token_count": len(text.split()),
        "text_preview": text[:120],
    }
