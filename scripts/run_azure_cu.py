#!/usr/bin/env python3
"""
Pre-compute Azure AI Content Understanding analysis for demo surahs.

Creates a custom analyzer (quran-audio-v1) and submits each surah's audio URL
for analysis, saving results to:

    data/output/{surah:03d}_{reciter}_cu.json

The Flask demo route (/api/demo/content-understanding/<n>) will serve this
file when present, otherwise falling back to synthetic data.

Usage:
    python scripts/run_azure_cu.py --setup-analyzer       # one-time setup
    python scripts/run_azure_cu.py --surah 1 112 114
    python scripts/run_azure_cu.py --setup-analyzer --surah 1 112 114

Environment (set in .env or CI secrets):
    AZURE_AI_ENDPOINT     – e.g. https://cog-yj5rva4yxmga2.cognitiveservices.azure.com
    AZURE_AI_KEY          – key for the Azure AI Services account
    OUTPUT_DIR            – path to data/output (default: ./data/output)
"""

from __future__ import annotations

import argparse
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
API_VERSION = "2024-12-01-preview"
ANALYZER_ID = "quran-audio-v1"
OUTPUT_DIR  = Path(os.getenv("OUTPUT_DIR", "./data/output"))

HEADERS = {"Ocp-Apim-Subscription-Key": KEY, "Content-Type": "application/json"}


def _cu(path: str) -> str:
    return f"{ENDPOINT}/contentunderstanding/{path}?api-version={API_VERSION}"


# ── analyzer setup ────────────────────────────────────────────────────────────

ANALYZER_SCHEMA = {
    "description": "Analyzes Quranic audio recitations — language, recitation style, and semantic fields",
    "scenario": "audioContent",
    "fieldSchema": {
        "name": "QuranAudioFields",
        "fields": {
            "language": {
                "type": "string",
                "description": "Detected BCP-47 language code (e.g. ar-SA)",
            },
            "script": {
                "type": "string",
                "description": "Writing script (e.g. Arabic Uthmani script)",
            },
            "recitationStyle": {
                "type": "string",
                "description": "Islamic recitation tradition (e.g. Tajweed, Hafs an Asim)",
            },
            "contentCategory": {
                "type": "string",
                "description": "Category / purpose of the recitation segment",
            },
            "overallSentiment": {
                "type": "string",
                "description": "Dominant emotional quality (reverent, declarative, supplicatory…)",
            },
            "topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Main theological or thematic topics identified",
            },
        },
    },
}


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
    """Create or update the quran-audio-v1 analyzer."""
    url  = _cu(f"analyzers/{ANALYZER_ID}")
    resp = requests.put(url, json=ANALYZER_SCHEMA, headers=HEADERS, timeout=30)
    if resp.status_code in (200, 201):
        print(f"✓ Analyzer '{ANALYZER_ID}' created/updated")
        return
    if resp.status_code == 202:
        op_url = resp.headers.get("Operation-Location", "")
        print(f"  async creation started — polling {op_url}")
        _wait_operation(op_url)
        print(f"✓ Analyzer '{ANALYZER_ID}' ready")
        return
    resp.raise_for_status()


# ── analysis ──────────────────────────────────────────────────────────────────

def submit_analysis(audio_url: str) -> str:
    """Submit audio URL for analysis, return the Operation-Location URL."""
    url  = _cu(f"analyzers/{ANALYZER_ID}:analyze")
    resp = requests.post(url, json={"url": audio_url}, headers=HEADERS, timeout=30)
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
    if not audio_url:
        print(f"[skip] No audio URL in {src_path.name}", file=sys.stderr)
        return

    out_path = OUTPUT_DIR / src_path.name.replace(".json", "_cu.json")
    if out_path.exists():
        print(f"[skip] {out_path.name} already exists")
        return

    print(f"Surah {surah_num} — submitting to Azure AI Content Understanding…")
    t0    = time.time()
    op    = submit_analysis(audio_url)
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
        "source_audio":      audio_url,
        # raw Azure CU result — Flask route normalises this for the frontend
        "raw_result":        result.get("result", result),
    }

    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ saved {out_path.name}  (processed in {proc_ms}ms)")


if __name__ == "__main__":
    if not ENDPOINT or not KEY:
        sys.exit("AZURE_AI_ENDPOINT and AZURE_AI_KEY must both be set.")

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
