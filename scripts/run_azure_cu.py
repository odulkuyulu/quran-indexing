#!/usr/bin/env python3
"""
Pre-compute Azure AI Content Understanding analysis for demo surahs.

Uses the prebuilt-audioSearch analyzer (GA API 2025-11-01) — no custom model
deployments required.  Results are saved to:

    data/output/{surah:03d}_{reciter}_cu.json

The Flask demo route (/api/demo/content-understanding/<n>) will serve this
file when present, otherwise falling back to synthetic data.

Usage:
    python scripts/run_azure_cu.py --surah 1 112 114
    python scripts/run_azure_cu.py --setup-analyzer      # no-op (kept for CI compat)
    python scripts/run_azure_cu.py --setup-analyzer --surah 1 112 114

Environment (set in .env or CI secrets):
    AZURE_AI_ENDPOINT     – e.g. https://cog-yj5rva4yxmga2.cognitiveservices.azure.com
    AZURE_AI_KEY          – key for the Azure AI Services account  (OR)
    AZURE_AD_TOKEN        – Entra ID bearer token (used when key auth is disabled)
    OUTPUT_DIR            – path to data/output (default: ./data/output)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

ENDPOINT    = os.getenv("AZURE_AI_ENDPOINT", "").rstrip("/")
KEY         = os.getenv("AZURE_AI_KEY", "")
AD_TOKEN    = os.getenv("AZURE_AD_TOKEN", "")   # bearer token — used when key auth is disabled
API_VERSION = "2025-11-01"
ANALYZER_ID = "prebuilt-audioSearch"  # built-in, no deployment required
OUTPUT_DIR  = Path(os.getenv("OUTPUT_DIR", "./data/output"))
AUDIO_DIR   = Path(os.getenv("AUDIO_DIR", "./data/audio"))


def _auth_headers() -> dict:
    """Return authentication headers, preferring bearer token (works when key auth is disabled)."""
    if AD_TOKEN:
        return {"Authorization": f"Bearer {AD_TOKEN}", "Content-Type": "application/json"}
    if KEY:
        return {"Ocp-Apim-Subscription-Key": KEY, "Content-Type": "application/json"}
    raise RuntimeError(
        "No Azure credentials available — set AZURE_AI_KEY or AZURE_AD_TOKEN"
    )


HEADERS = _auth_headers()


def _cu(path: str) -> str:
    return f"{ENDPOINT}/contentunderstanding/{path}?api-version={API_VERSION}"


def _wait_operation(op_url: str, max_wait: int = 120) -> dict:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        resp = requests.get(op_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "")
        print(f"  op status: {status}", flush=True)
        if status.lower() in ("succeeded", "completed"):
            return data
        if status.lower() == "failed":
            raise RuntimeError(f"Operation failed: {data}")
        time.sleep(5)
    raise TimeoutError("Operation did not complete in time")


def setup_analyzer() -> None:
    """No-op: prebuilt-audioSearch requires no setup.  Kept for CI script compat."""
    print(f"✓ Using prebuilt analyzer '{ANALYZER_ID}' — no setup required")


# ── analysis ──────────────────────────────────────────────────────────────────

def submit_analysis(audio_url: str) -> str:
    """Submit audio URL for analysis, return the Operation-Location URL."""
    url  = _cu(f"analyzers/{ANALYZER_ID}:analyze")
    # GA API (2025-11-01) requires inputs array
    payload = {"inputs": [{"url": audio_url}]}
    resp = requests.post(url, json=payload, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.headers["Operation-Location"]


def submit_analysis_binary(audio_path: Path) -> str:
    """Submit local audio file as base64-encoded binary (avoids CDN/Cloudflare blocks)."""
    url  = _cu(f"analyzers/{ANALYZER_ID}:analyze")
    suffix = audio_path.suffix.lower()
    mime = "audio/wav" if suffix == ".wav" else "audio/mpeg"
    with open(audio_path, "rb") as fh:
        audio_b64 = base64.b64encode(fh.read()).decode("ascii")
    payload = {"inputs": [{"data": audio_b64, "mimeType": mime}]}
    resp = requests.post(url, json=payload, headers=HEADERS, timeout=60)
    resp.raise_for_status()
    return resp.headers["Operation-Location"]


def wait_analysis(op_url: str, poll: int = 8, max_wait: int = 600) -> dict:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        resp = requests.get(op_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status", "")
        print(f"  status: {status}", flush=True)
        if status.lower() in ("succeeded", "completed"):
            return data
        if status.lower() == "failed":
            raise RuntimeError(f"Analysis failed: {data}")
        time.sleep(poll)
    raise TimeoutError("Analysis did not complete in time")


def _source_json(surah_num: int) -> tuple[Path, dict] | tuple[None, None]:
    candidates = [
        f for f in OUTPUT_DIR.glob(f"{surah_num:03d}_*.json")
        if not any(x in f.name for x in ("_speech", "_cu"))
    ]
    if not candidates:
        return None, None
    path = candidates[0]
    with open(path, encoding="utf-8") as fh:
        return path, json.load(fh)


def process_surah(surah_num: int) -> None:
    src_path, src = _source_json(surah_num)
    if src is None:
        print(f"[skip] No alignment JSON for surah {surah_num}", file=sys.stderr)
        return

    audio_url = src.get("source_audio_url") or ""
    if not audio_url:
        ayahs = src.get("ayahs") or []
        audio_url = ayahs[0].get("audio_url", "") if ayahs else ""

    out_path = OUTPUT_DIR / src_path.name.replace(".json", "_cu.json")
    if out_path.exists():
        print(f"[skip] {out_path.name} already exists")
        return

    # Prefer local normalized WAV — avoids Cloudflare/CDN blocks on external URLs
    reciter = src.get("reciter", "mishary_alafasy")
    local_wav = AUDIO_DIR / "normalized" / f"{surah_num:03d}_{reciter}_normalized.wav"

    print(f"Surah {surah_num} — submitting to Azure AI Content Understanding…")
    t0 = time.time()

    if local_wav.exists():
        print(f"  source: local WAV {local_wav.name} ({local_wav.stat().st_size // 1024} KB)")
        op = submit_analysis_binary(local_wav)
        source_audio = str(local_wav)
    elif audio_url:
        print(f"  source: remote URL {audio_url}")
        op = submit_analysis(audio_url)
        source_audio = audio_url
    else:
        print(f"[skip] No audio source for surah {surah_num}", file=sys.stderr)
        return

    print(f"  operation: {op}")
    result = wait_analysis(op)
    proc_ms = int((time.time() - t0) * 1000)

    output = {
        "surah":             src.get("surah"),
        "name_english":      src.get("name_english"),
        "name_arabic":       src.get("name_arabic"),
        "reciter":           src.get("reciter"),
        "service":           "Azure AI Content Understanding",
        "analyzer_id":       ANALYZER_ID,
        "api_version":       API_VERSION,
        "status":            "Succeeded",
        "processing_time_ms": proc_ms,
        "source_audio":      source_audio,
        # raw Azure CU result — Flask route normalises this for the frontend
        "raw_result":        result.get("result", result),
    }

    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ saved {out_path.name}  (processed in {proc_ms}ms)")


if __name__ == "__main__":
    if not ENDPOINT:
        sys.exit("AZURE_AI_ENDPOINT must be set.")
    if not KEY and not AD_TOKEN:
        sys.exit("Either AZURE_AI_KEY or AZURE_AD_TOKEN must be set.")

    parser = argparse.ArgumentParser(description="Run Azure AI Content Understanding on surahs.")
    parser.add_argument("--setup-analyzer", action="store_true",
                        help="Create / update the quran-audio-v1 analyzer (run once)")
    parser.add_argument("--surah", type=int, nargs="+",
                        metavar="N", help="Surah number(s) to process")
    args = parser.parse_args()

    if args.setup_analyzer:
        setup_analyzer()

    if args.surah:
        for surah in args.surah:
            try:
                process_surah(surah)
            except Exception as exc:
                print(f"[error] Surah {surah}: {exc}", file=sys.stderr)
