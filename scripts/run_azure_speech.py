#!/usr/bin/env python3
"""
Pre-compute Azure Speech batch transcription for demo surahs.

Submits each surah's audio URL to Azure Speech batch transcription (ar-SA),
polls until done, then saves word-level results to:

    data/output/{surah:03d}_{reciter}_speech.json

The Flask demo route (/api/demo/speech/<n>) will serve this file if present,
otherwise falling back to synthetic data built from alignment results.

Usage:
    python scripts/run_azure_speech.py --surah 1 112 114

Environment (set in .env or CI secrets):
    AZURE_AI_KEY          – key for the Azure AI Services account
    AZURE_SPEECH_REGION   – Azure region, e.g. westeurope
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

KEY     = os.getenv("AZURE_AI_KEY", "")
AD_TOKEN = os.getenv("AZURE_AD_TOKEN", "")  # bearer token — used when key auth is disabled
REGION  = os.getenv("AZURE_SPEECH_REGION", "westeurope")
ENDPOINT = os.getenv("AZURE_AI_ENDPOINT", "").rstrip("/")  # custom subdomain — required for token auth
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./data/output"))

# Bearer-token auth requires the custom subdomain endpoint; key auth uses the regional endpoint
if AD_TOKEN and ENDPOINT:
    SPEECH_API = f"{ENDPOINT}/speechtotext/v3.1"
else:
    SPEECH_API = f"https://{REGION}.api.cognitive.microsoft.com/speechtotext/v3.1"


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


# ── helpers ──────────────────────────────────────────────────────────────────

def _source_json(surah_num: int) -> tuple[Path, dict] | tuple[None, None]:
    """Return (path, data) for the alignment JSON of this surah."""
    candidates = [
        f for f in OUTPUT_DIR.glob(f"{surah_num:03d}_*.json")
        if not any(x in f.name for x in ("_speech", "_cu"))
    ]
    if not candidates:
        return None, None
    path = candidates[0]
    with open(path, encoding="utf-8") as fh:
        return path, json.load(fh)


def submit_job(audio_url: str, display_name: str) -> str:
    """POST a batch transcription job, return the job URL."""
    payload = {
        "contentUrls": [audio_url],
        "locale": "ar-SA",
        "displayName": display_name,
        "properties": {
            "wordLevelTimestampsEnabled": True,
            "punctuationMode": "None",
            "profanityFilterMode": "None",
            "diarizationEnabled": False,
        },
    }
    resp = requests.post(f"{SPEECH_API}/transcriptions", json=payload,
                         headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.headers["Location"]


def wait_job(job_url: str, poll: int = 8, max_wait: int = 600) -> dict:
    """Poll until Succeeded/Failed, return job dict."""
    deadline = time.time() + max_wait
    while time.time() < deadline:
        data = requests.get(job_url, headers=HEADERS, timeout=30).json()
        status = data.get("status", "")
        print(f"  status: {status}", flush=True)
        if status == "Succeeded":
            return data
        if status == "Failed":
            raise RuntimeError(f"Job failed: {data.get('properties',{}).get('error',{})}")
        time.sleep(poll)
    raise TimeoutError("Transcription did not complete in time")


def get_result(job_url: str) -> dict:
    """Fetch the Transcription result file and return its JSON."""
    files = requests.get(f"{job_url.rstrip('/')}/files",
                         headers=HEADERS, timeout=30).json().get("values", [])
    result_file = next((f for f in files if f.get("kind") == "Transcription"), None)
    if not result_file:
        raise RuntimeError("No Transcription result file found")
    return requests.get(result_file["links"]["contentUrl"], timeout=60).json()


# ── main logic ───────────────────────────────────────────────────────────────

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

    out_path = OUTPUT_DIR / src_path.name.replace(".json", "_speech.json")
    if out_path.exists():
        print(f"[skip] {out_path.name} already exists")
        return

    print(f"Surah {surah_num} — submitting to Azure Speech (ar-SA, {REGION})…")
    job_url = submit_job(audio_url, f"Surah {surah_num} — {src.get('name_english','')}")
    print(f"  job: {job_url}")

    wait_job(job_url)
    raw = get_result(job_url)

    # Normalise into our storage format
    phrases = []
    for seg in raw.get("recognizedPhrases", []):
        best = (seg.get("nBest") or [{}])[0]
        phrases.append({
            "offset_ms":   int(seg.get("offsetInTicks", 0) / 1e4),
            "duration_ms": int(seg.get("durationInTicks", 0) / 1e4),
            "text":        best.get("display", ""),
            "lexical":     best.get("lexical", ""),
            "confidence":  round(best.get("confidence", 0.0), 4),
            "words": [
                {
                    "word":        w.get("word", ""),
                    "offset_ms":   int(w.get("offsetInTicks", 0) / 1e4),
                    "duration_ms": int(w.get("durationInTicks", 0) / 1e4),
                    "confidence":  round(w.get("confidence", 0.0), 4),
                }
                for w in best.get("words", [])
            ],
        })

    result = {
        "surah":         src.get("surah"),
        "name_english":  src.get("name_english"),
        "name_arabic":   src.get("name_arabic"),
        "reciter":       src.get("reciter"),
        "service":       "Azure Speech Service",
        "model":         "ar-SA_ConversationalTranscription",
        "region":        REGION,
        "language":      "ar-SA",
        "duration_ms":   src.get("audio_duration_ms"),
        "source_audio":  audio_url,
        "word_count":    sum(len(p["words"]) for p in phrases),
        "phrases":       phrases,
    }

    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ✓ saved {out_path.name}  "
          f"({len(phrases)} phrases, {result['word_count']} words)")


if __name__ == "__main__":
    if not KEY and not AD_TOKEN:
        sys.exit("Either AZURE_AI_KEY or AZURE_AD_TOKEN must be set.")

    parser = argparse.ArgumentParser(description="Run Azure Speech batch transcription.")
    parser.add_argument("--surah", type=int, nargs="+", required=True,
                        metavar="N", help="Surah number(s) to process")
    args = parser.parse_args()

    for surah in args.surah:
        try:
            process_surah(surah)
        except Exception as exc:
            print(f"[error] Surah {surah}: {exc}", file=sys.stderr)
