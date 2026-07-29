"""
Seed Demo Data
==============
Fetches canonical Uthmani text from Quran.com API v4 and generates a
properly-formatted index JSON for short surahs — WITHOUT needing WhisperX
or a GPU.

Timestamps are EVENLY DISTRIBUTED across the surah duration (not aligned).
Each ayah is clearly flagged with `alignment_method: "estimated"` and
`needs_review: true` so the UI can show a warning.

Usage:
    python scripts/seed_demo.py
    python scripts/seed_demo.py --surahs 1 112 114 --reciter mishary_alafasy
"""

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()

# ---------------------------------------------------------------------------
# Quran.com API
# ---------------------------------------------------------------------------

QURANCOM_BASE = "https://api.quran.com/api/v4"

# Per-reciter configuration.
# qurancom_id: Quran.com recitation ID (None = not available there)
# audio_pattern: fallback direct URL pattern when qurancom_id is None
# qiraa: recitation style label
RECITER_CONFIGS: dict[str, dict] = {
    "mishary_alafasy": {
        "display": "Mishary Rashid Alafasy",
        "qurancom_id": 7,
        "audio_pattern": "https://download.quranicaudio.com/quran/mishaari_raashid_al-3afaasee/{surah:03d}.mp3",
        "qiraa": "Hafs",
    },
    "khalifa_al_kuwari": {
        "display": "Khalifa Al-Kuwari",
        "qurancom_id": None,  # not in Quran.com recitations list
        "audio_pattern": "https://download.quranicaudio.com/quran/khalefa_al_kuwari/{surah:03d}.mp3",
        "qiraa": "Hafs",
    },
}

# Known short surahs good for demo (surah_num: approx_duration_ms)
DEFAULT_SURAHS = [1, 112, 114]


def qurancom_get(path: str, **params) -> dict:
    """GET from Quran.com API with basic retry."""
    url = f"{QURANCOM_BASE}{path}"
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=15)
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if attempt == 2:
                raise
            console.print(f"[yellow]Retry {attempt + 1}: {e}[/yellow]")
            time.sleep(2)


def fetch_chapter_meta(surah: int) -> dict:
    """Return chapter metadata from Quran.com."""
    data = qurancom_get(f"/chapters/{surah}")
    ch = data["chapter"]
    return {
        "surah": surah,
        "name_arabic": ch["name_arabic"],
        "name_english": ch["name_simple"],
        "ayah_count": ch["verses_count"],
    }


def fetch_verses(surah: int) -> list:
    """Return list of {ayah, text_uthmani} for the surah."""
    data = qurancom_get(
        f"/verses/by_chapter/{surah}",
        fields="text_uthmani",
        per_page=300,
    )
    return [
        {
            "ayah": v["verse_number"],
            "text_uthmani": v["text_uthmani"],
        }
        for v in data["verses"]
    ]


def fetch_audio_url(surah: int, reciter: str = "mishary_alafasy") -> str:
    """Get full-surah audio URL for the given reciter."""
    cfg = RECITER_CONFIGS.get(reciter, RECITER_CONFIGS["mishary_alafasy"])
    recitation_id = cfg["qurancom_id"]

    if recitation_id is not None:
        # Try Quran.com API first
        try:
            data = qurancom_get(f"/chapter_recitations/{recitation_id}/{surah}")
            return data["audio_file"]["audio_url"]
        except Exception:
            pass  # fall through to direct pattern

    # Use direct audio pattern
    return cfg["audio_pattern"].format(surah=surah)


# ---------------------------------------------------------------------------
# Audio duration (ffprobe)
# ---------------------------------------------------------------------------

# Known durations (ms) per reciter — used when ffprobe unavailable.
# Source: QuranicAudio.com measurements.
KNOWN_DURATIONS_MS: dict[str, dict[int, int]] = {
    "mishary_alafasy": {
        1:   46_000,   # Al-Fatiha      ~46s
        2: 5580_000,   # Al-Baqara    ~93min
        67:  197_000,  # Al-Mulk       ~3m17s
        78:  177_000,  # An-Naba       ~2m57s
        112:  17_000,  # Al-Ikhlas      ~17s
        113:  15_000,  # Al-Falaq       ~15s
        114:  21_000,  # An-Nas         ~21s
    },
    "khalifa_al_kuwari": {
        1:   55_000,   # Al-Fatiha      ~55s
        112:  20_000,  # Al-Ikhlas      ~20s
        113:  19_000,  # Al-Falaq       ~19s
        114:  25_000,  # An-Nas         ~25s
    },
}


def get_duration_ms(surah: int, mp3_path: Path, reciter: str = "mishary_alafasy") -> int:
    """Return duration in ms. Tries ffprobe first, then known table, then 0."""
    # Try ffprobe
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                str(mp3_path),
            ],
            capture_output=True, text=True, timeout=30,
        )
        info = json.loads(result.stdout)
        secs = float(info["format"]["duration"])
        return int(secs * 1000)
    except Exception:
        pass

    # Try mutagen (lightweight MP3 metadata reader)
    try:
        from mutagen.mp3 import MP3
        audio = MP3(str(mp3_path))
        return int(audio.info.length * 1000)
    except Exception:
        pass

    # Fall back to known table
    reciter_durations = KNOWN_DURATIONS_MS.get(reciter, KNOWN_DURATIONS_MS["mishary_alafasy"])
    if surah in reciter_durations:
        dur = reciter_durations[surah]
        console.print(f"  [dim]Using known duration fallback: {dur / 1000:.0f}s[/dim]")
        return dur

    console.print("  [yellow]Duration unknown — timestamps will be 0[/yellow]")
    return 0


def download_mp3(audio_url: str, dest: Path) -> bool:
    """Download surah MP3. Returns True on success."""
    if dest.exists():
        console.print(f"  [dim]Audio cached: {dest.name}[/dim]")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        console.print(f"  Downloading audio → {dest.name} …")
        with requests.get(audio_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
        return True
    except Exception as e:
        console.print(f"  [yellow]Audio download failed: {e}[/yellow]")
        return False


# ---------------------------------------------------------------------------
# Build output JSON
# ---------------------------------------------------------------------------

def distribute_timestamps(ayah_count: int, duration_ms: int) -> list[tuple[int, int]]:
    """
    Divide total duration evenly across ayahs.
    Returns list of (start_ms, end_ms) tuples.
    """
    if duration_ms <= 0 or ayah_count <= 0:
        return [(0, 0)] * ayah_count
    slice_ms = duration_ms // ayah_count
    spans = []
    for i in range(ayah_count):
        start = i * slice_ms
        end = (i + 1) * slice_ms if i < ayah_count - 1 else duration_ms
        spans.append((start, end))
    return spans


def compute_text_hash(verses: list) -> str:
    combined = "".join(v["text_uthmani"] for v in verses)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]


def build_index(
    meta: dict,
    verses: list,
    audio_url: str,
    duration_ms: int,
    reciter: str,
) -> dict:
    cfg = RECITER_CONFIGS.get(reciter, RECITER_CONFIGS["mishary_alafasy"])
    qiraa = cfg["qiraa"]
    display_name = cfg["display"]
    spans = distribute_timestamps(meta["ayah_count"], duration_ms)
    ayahs = []
    for v, (start, end) in zip(verses, spans):
        ayahs.append({
            "surah": meta["surah"],
            "ayah": v["ayah"],
            "reciter": display_name,
            "qiraa": qiraa,
            "audio_url": audio_url,
            "start_ms": start,
            "end_ms": end,
            "text_uthmani": v["text_uthmani"],
            "confidence": 0.0,
            "needs_review": True,
        })

    return {
        "surah": meta["surah"],
        "name_arabic": meta["name_arabic"],
        "name_english": meta["name_english"],
        "reciter": display_name,
        "qiraa": qiraa,
        "source_audio_url": audio_url,
        "audio_duration_ms": duration_ms,
        "ayah_count": meta["ayah_count"],
        "ayahs": ayahs,
        # Metadata
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": "seed-demo-1.0",
        "alignment_method": "estimated",          # NOT forced-aligned
        "alignment_note": (
            "Timestamps are evenly distributed estimates only. "
            "Run the full pipeline with WhisperX for accurate alignment."
        ),
        "text_hash": compute_text_hash(verses),
        "model_used": "none",
        "mean_confidence": 0.0,
        "ayahs_needing_review": meta["ayah_count"],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def seed_surah(
    surah: int,
    reciter: str,
    audio_dir: Path,
    output_dir: Path,
) -> bool:
    console.rule(f"Surah {surah}")

    # 1. Fetch text
    console.print("  Fetching chapter metadata …")
    meta = fetch_chapter_meta(surah)
    console.print(
        f"  [green]{meta['name_english']}[/green] ({meta['name_arabic']}) "
        f"— {meta['ayah_count']} ayahs"
    )

    console.print("  Fetching canonical Uthmani text …")
    verses = fetch_verses(surah)
    if len(verses) != meta["ayah_count"]:
        console.print(
            f"  [red]Verse count mismatch: got {len(verses)}, "
            f"expected {meta['ayah_count']}[/red]"
        )
        return False

    # 2. Audio
    audio_url = fetch_audio_url(surah, reciter)
    mp3_dest = audio_dir / "raw" / f"{surah:03d}_{reciter}.mp3"
    downloaded = download_mp3(audio_url, mp3_dest)
    duration_ms = get_duration_ms(surah, mp3_dest, reciter) if downloaded else KNOWN_DURATIONS_MS.get(reciter, {}).get(surah, 0)

    if duration_ms:
        console.print(f"  Duration: {duration_ms / 1000:.1f}s")
    else:
        console.print("  [yellow]Duration unknown — timestamps will be 0[/yellow]")

    # 3. Build + write JSON
    index = build_index(meta, verses, audio_url, duration_ms, reciter)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{surah:03d}_{reciter}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    console.print(f"  [green]✓ Written:[/green] {out_path}")
    return True


def main():
    parser = argparse.ArgumentParser(description="Seed demo data from Quran.com API")
    parser.add_argument(
        "--surahs", nargs="+", type=int,
        default=DEFAULT_SURAHS,
        help="Surah numbers to seed (default: 1 112 114)",
    )
    available = ", ".join(RECITER_CONFIGS.keys())
    parser.add_argument(
        "--reciter", default="mishary_alafasy",
        help=f"Reciter slug. Available: {available}",
    )
    parser.add_argument(
        "--audio-dir", default="./data/audio", type=Path,
    )
    parser.add_argument(
        "--output-dir", default="./data/output", type=Path,
    )
    args = parser.parse_args()

    display = RECITER_CONFIGS.get(args.reciter, {}).get("display", args.reciter)
    console.print(Panel(
        f"[bold cyan]Quran Demo Seeder[/bold cyan]\n\n"
        f"Surahs : {args.surahs}\n"
        f"Reciter: {display} ({args.reciter})\n"
        f"Output : {args.output_dir}\n\n"
        "[yellow]NOTE: timestamps are ESTIMATED (even distribution).\n"
        "Run full pipeline for real forced-alignment.[/yellow]",
        title="Seed Demo",
    ))

    ok, fail = 0, 0
    for surah in args.surahs:
        if seed_surah(surah, args.reciter, args.audio_dir, args.output_dir):
            ok += 1
        else:
            fail += 1

    console.print()
    if fail == 0:
        console.print(f"[bold green]Done — {ok} surah(s) seeded.[/bold green]")
    else:
        console.print(
            f"[bold yellow]{ok} seeded, {fail} failed.[/bold yellow]"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
