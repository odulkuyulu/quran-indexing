"""
Demo App - Flask UI for Quran Ayah Lookup

A simple interface to:
1. Look up any surah:ayah by reference
2. Display the canonical Uthmani text
3. Play the exact audio slice [start_ms, end_ms]

Usage:
    python app/demo.py
    # Then open http://localhost:5000
"""

import os
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Dict

from flask import Flask, render_template, jsonify, abort, send_file, request
from dotenv import load_dotenv

load_dotenv()

# Configuration
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./data/output"))
AUDIO_DIR = Path(os.getenv("AUDIO_DIR", "./data/audio"))
SEARCH_ENDPOINT = os.getenv("AZURE_SEARCH_ENDPOINT", "")
SEARCH_KEY = os.getenv("AZURE_SEARCH_KEY", "")
SEARCH_INDEX = os.getenv("AZURE_SEARCH_INDEX", "quran-ayah-index")

app = Flask(__name__, template_folder="templates")


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def load_available_indices() -> Dict[int, Dict]:
    """Return metadata for every indexed surah in OUTPUT_DIR."""
    indices: Dict[int, Dict] = {}
    if not OUTPUT_DIR.exists():
        return indices
    for json_file in OUTPUT_DIR.glob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            surah_num = data.get("surah")
            if surah_num:
                indices[surah_num] = {
                    "path": str(json_file),
                    "name_english": data.get("name_english", f"Surah {surah_num}"),
                    "name_arabic": data.get("name_arabic", ""),
                    "reciter": data.get("reciter", "Unknown"),
                    "ayah_count": data.get("ayah_count", 0),
                }
        except (json.JSONDecodeError, KeyError):
            continue
    return indices


def load_surah_data(json_path: str) -> Optional[Dict]:
    """Load full surah JSON from disk."""
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def extract_audio_slice(surah: int, reciter: str, start_ms: int, end_ms: int) -> Optional[Path]:
    """Use ffmpeg to cut an ayah slice and cache it in the system temp dir."""
    normalized = AUDIO_DIR / "normalized" / f"{surah:03d}_{reciter}_normalized.wav"
    raw = AUDIO_DIR / "raw" / f"{surah:03d}_{reciter}.mp3"

    if normalized.exists():
        source = normalized
    elif raw.exists():
        source = raw
    else:
        return None

    out = Path(tempfile.gettempdir()) / f"ayah_{surah}_{start_ms}_{end_ms}.mp3"
    if out.exists():
        return out

    cmd = [
        "ffmpeg", "-y",
        "-i", str(source),
        "-ss", str(start_ms / 1000),
        "-t", str((end_ms - start_ms) / 1000),
        "-c:a", "libmp3lame", "-q:a", "2",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return out if result.returncode == 0 and out.exists() else None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    indices = load_available_indices()
    surahs = [
        {"num": num, **info}
        for num, info in sorted(indices.items())
    ]
    return render_template("index.html", surahs=surahs)


@app.route("/api/surah/<int:surah_num>")
def api_surah(surah_num: int):
    indices = load_available_indices()
    info = indices.get(surah_num)
    if not info:
        abort(404, description="Surah not indexed yet")
    data = load_surah_data(info["path"])
    if not data:
        abort(500, description="Failed to load surah data")
    return jsonify(data)


@app.route("/api/ayah/<int:surah_num>/<int:ayah_num>")
def api_ayah(surah_num: int, ayah_num: int):
    indices = load_available_indices()
    info = indices.get(surah_num)
    if not info:
        abort(404, description="Surah not indexed yet")
    data = load_surah_data(info["path"])
    if not data:
        abort(500)
    ayah = next((a for a in data.get("ayahs", []) if a.get("ayah") == ayah_num), None)
    if not ayah:
        abort(404, description=f"Ayah {ayah_num} not found")
    return jsonify(ayah)


@app.route("/audio/<int:surah_num>/<int:start_ms>/<int:end_ms>")
def audio_slice(surah_num: int, start_ms: int, end_ms: int):
    reciter = request.args.get("reciter", "unknown")
    path = extract_audio_slice(surah_num, reciter, start_ms, end_ms)
    if not path:
        abort(404, description="Audio file not found. Run the pipeline first.")
    return send_file(str(path), mimetype="audio/mpeg")


@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"results": [], "count": 0, "error": None})

    if not SEARCH_ENDPOINT or not SEARCH_KEY:
        return jsonify({"results": [], "count": 0, "error": "Search not configured"})

    semantic = request.args.get("semantic", "false").lower() == "true"

    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from src.search_loader import search_ayahs
        hits = search_ayahs(
            query=q,
            endpoint=SEARCH_ENDPOINT,
            key=SEARCH_KEY,
            index_name=SEARCH_INDEX,
            top=12,
            semantic=semantic,
        )
        return jsonify({"results": hits, "count": len(hits), "error": None, "semantic": semantic})
    except Exception as e:
        return jsonify({"results": [], "count": 0, "error": str(e)})


@app.route("/api/search/status")
def api_search_status():
    return jsonify({"configured": bool(SEARCH_ENDPOINT and SEARCH_KEY)})


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"Starting Quran Ayah Lookup on http://localhost:{port}")
    app.run(debug=True, host="0.0.0.0", port=port)

