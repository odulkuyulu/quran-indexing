#!/usr/bin/env python3
"""
Fix ayah timestamps for surahs 112 and 114 using Azure Speech word timings.

The original WhisperX alignment included the leading Basmala words in ayah-1's
word budget, shifting every ayah boundary forward by ~3-5 seconds.
The *_speech.json files have accurate word-level timings (the Speech Service
correctly placed the Basmala), so we use them as ground truth to re-derive
the ayah start/end times.

Surah 1 (Al-Fatiha) is not touched because the Basmala IS ayah 1 there.

Usage:
    python scripts/fix_timestamps_from_speech.py
"""
from __future__ import annotations

import json
import re
from pathlib import Path

OUTPUT_DIR = Path("./data/output")

BASMALA_NORMALIZED = ["بسم", "الله", "الرحمن", "الرحيم"]


def _normalize(word: str) -> str:
    """Strip diacritics/tatweel and normalize alef variants — mirrors align.py."""
    word = re.sub(r"[\u064B-\u065F\u0670]", "", word)  # tashkeel
    word = re.sub(r"\u0640", "", word)                  # tatweel
    word = re.sub(r"[إأآا]", "ا", word)                # alef variants
    return word.strip()


def _word_count(text_uthmani: str) -> int:
    return len([w for w in text_uthmani.split() if w.strip()])


def _flatten_words(speech: dict) -> list[dict]:
    """
    Flatten phrase words into a single list, sorted by start_ms.
    Removes exact duplicates (same start_ms + same normalized word) that
    arise when the Speech Service returns multiple hypotheses for the same audio.
    Returns only the FIRST occurrence of each (start_ms, norm_word) pair.
    """
    seen: set[tuple] = set()
    result = []
    for phrase in speech.get("phrases", []):
        for w in phrase.get("words", []):
            key = (w["offset_ms"], _normalize(w["word"]))
            if key not in seen:
                seen.add(key)
                result.append({
                    "word":       w["word"],
                    "start_ms":   w["offset_ms"],
                    "end_ms":     w["offset_ms"] + w["duration_ms"],
                    "confidence": w.get("confidence", 0.0),
                })
    return sorted(result, key=lambda x: x["start_ms"])


def _find_first_word(
    words: list[dict],
    target: str,
    search_from_ms: int = 0,
) -> int:
    """
    Return the index of the first word whose normalized form matches `target`
    and whose start_ms >= search_from_ms.  Returns -1 if not found.
    """
    t = _normalize(target)
    for i, w in enumerate(words):
        if w["start_ms"] >= search_from_ms and _normalize(w["word"]) == t:
            return i
    return -1


def fix_surah(surah_num: int, reciter: str) -> None:
    speech_path = OUTPUT_DIR / f"{surah_num:03d}_{reciter}_speech.json"
    align_path  = OUTPUT_DIR / f"{surah_num:03d}_{reciter}.json"

    if not speech_path.exists():
        print(f"  [skip] No speech JSON: {speech_path.name}")
        return
    if not align_path.exists():
        print(f"  [skip] No alignment JSON: {align_path.name}")
        return

    with open(speech_path, encoding="utf-8") as f:
        speech = json.load(f)
    with open(align_path, encoding="utf-8") as f:
        align = json.load(f)

    words = _flatten_words(speech)
    print(f"Surah {surah_num}: {len(words)} words after dedup")

    # Find end of Basmala: last word in the leading Basmala sequence
    basmala_end_ms = 0
    bi = 0
    for expected in BASMALA_NORMALIZED:
        if bi < len(words) and _normalize(words[bi]["word"]) == expected:
            basmala_end_ms = words[bi]["end_ms"]
            bi += 1
    if bi > 0:
        content_words = words[bi:]
        print(f"  Basmala stripped ({bi} words, ends at {basmala_end_ms} ms)")
    else:
        content_words = words
        print("  WARNING: Basmala not found — proceeding without strip")

    print(f"  {len(content_words)} content words remain")

    ayahs = align["ayahs"]
    audio_end_ms = align["audio_duration_ms"]

    # ------------------------------------------------------------------
    # For each ayah anchor its start to the first occurrence of its
    # canonical first word in content_words (searching forward from the
    # previous ayah's anchor position in time).
    # ------------------------------------------------------------------
    ayah_word_indices: list[int] = []
    min_start_ms = 0  # must be at or after the Basmala

    for ayah in ayahs:
        first_word = [w for w in ayah["text_uthmani"].split() if w.strip()][0]
        idx = _find_first_word(content_words, first_word, min_start_ms)
        if idx == -1:
            # Fall back to next available word
            idx = len(ayah_word_indices)
            print(f"  WARNING: first word of Ayah {ayah['ayah']} not found; using index {idx}")
        ayah_word_indices.append(idx)
        min_start_ms = content_words[idx]["start_ms"] + 1  # strict forward progress

    # Each ayah owns content_words[start_idx : next_start_idx]
    for i, ayah in enumerate(ayahs):
        s = ayah_word_indices[i]
        e = ayah_word_indices[i + 1] if i + 1 < len(ayahs) else len(content_words)
        chunk = content_words[s:e]

        if chunk:
            ayah["start_ms"]   = chunk[0]["start_ms"]
            ayah["end_ms"]     = chunk[-1]["end_ms"]
            ayah["confidence"] = round(
                sum(w["confidence"] for w in chunk) / len(chunk), 4
            )
        ayah["needs_review"] = ayah.get("confidence", 0.0) < 0.75

        print(
            f"  Ayah {ayah['ayah']:>2}: {ayah['start_ms']:>6} ms – {ayah['end_ms']:>6} ms"
            f"  ({_word_count(ayah['text_uthmani'])} canonical, {len(chunk)} speech words,"
            f"  conf={ayah['confidence']:.3f})"
        )

    # Last ayah always extends to end of audio
    if ayahs:
        ayahs[-1]["end_ms"] = audio_end_ms

    # Close gaps between consecutive ayahs (split the midpoint)
    for i in range(1, len(ayahs)):
        prev, curr = ayahs[i - 1], ayahs[i]
        if curr["start_ms"] > prev["end_ms"]:
            mid = (prev["end_ms"] + curr["start_ms"]) // 2
            prev["end_ms"]   = mid
            curr["start_ms"] = mid
        elif curr["start_ms"] < prev["end_ms"]:
            # Overlap — set current to start right at prev end
            curr["start_ms"] = prev["end_ms"]

    with open(align_path, "w", encoding="utf-8") as f:
        json.dump(align, f, ensure_ascii=False, indent=2)

    print(f"  ✓ Saved {align_path.name}\n")


if __name__ == "__main__":
    # Surah 1 (Al-Fatiha): Basmala IS ayah 1 — no fix needed
    # Surah 9 (At-Tawbah): no Basmala — no fix needed
    for s in [112, 114]:
        print(f"\n{'='*50}")
        fix_surah(s, "mishary_alafasy")
    print("All done.")



def _dedupe_words(words: list[dict]) -> list[dict]:
    """
    Remove duplicate word entries (same start_ms and same normalized text).
    The speech service sometimes returns multiple hypotheses for the same
    audio segment, producing duplicated word timings.
    """
    seen: set[tuple] = set()
    result = []
    for w in words:
        key = (w["start_ms"], _normalize(w["word"]))
        if key not in seen:
            seen.add(key)
            result.append(w)
    return result


def _find_first_word_position(
    speech_words: list[dict],
    canonical_first_word: str,
    search_from: int,
) -> int:
    """
    Return the index in speech_words of the first occurrence of
    canonical_first_word at or after search_from.
    Returns search_from if not found (safe fallback).
    """
    target = _normalize(canonical_first_word)
    for i in range(search_from, len(speech_words)):
        if _normalize(speech_words[i]["word"]) == target:
            return i
    return search_from


def fix_surah(surah_num: int, reciter: str) -> None:
    speech_path = OUTPUT_DIR / f"{surah_num:03d}_{reciter}_speech.json"
    align_path  = OUTPUT_DIR / f"{surah_num:03d}_{reciter}.json"

    if not speech_path.exists():
        print(f"  [skip] No speech JSON: {speech_path.name}")
        return
    if not align_path.exists():
        print(f"  [skip] No alignment JSON: {align_path.name}")
        return

    with open(speech_path, encoding="utf-8") as f:
        speech = json.load(f)
    with open(align_path, encoding="utf-8") as f:
        align = json.load(f)

    # Flatten, deduplicate, and sort all word timings by start_ms
    words: list[dict] = []
    for phrase in speech.get("phrases", []):
        for w in phrase.get("words", []):
            words.append({
                "word":       w["word"],
                "start_ms":   w["offset_ms"],
                "end_ms":     w["offset_ms"] + w["duration_ms"],
                "confidence": w.get("confidence", 0.0),
            })
    words = sorted(_dedupe_words(words), key=lambda x: x["start_ms"])

    print(f"Surah {surah_num}: {len(words)} speech words after dedup")

    # Strip leading Basmala
    stripped = 0
    for expected in BASMALA_NORMALIZED:
        if stripped < len(words) and _normalize(words[stripped]["word"]) == expected:
            stripped += 1

    if stripped > 0:
        basmala_end_ms = words[stripped - 1]["end_ms"]
        print(f"  Stripped {stripped} Basmala words (ends at {basmala_end_ms} ms)")
        words = words[stripped:]
    else:
        print("  WARNING: Basmala not found at start — timings may still be offset")

    print(f"  {len(words)} words remain for ayah mapping")

    ayahs = align["ayahs"]
    audio_end_ms = align["audio_duration_ms"]

    # Anchor each ayah start by locating its first canonical word in the
    # speech stream.  This is robust to missed/extra words from the ASR.
    ayah_starts: list[int] = []   # word index in `words` for each ayah
    search_pos = 0
    for ayah in ayahs:
        first_word = ayah["text_uthmani"].split()[0]
        idx = _find_first_word_position(words, first_word, search_pos)
        ayah_starts.append(idx)
        search_pos = idx + 1      # next ayah must start after this word

    # Build ayah slices: ayah[i] uses words[ayah_starts[i] : ayah_starts[i+1]]
    for i, ayah in enumerate(ayahs):
        s = ayah_starts[i]
        e = ayah_starts[i + 1] if i + 1 < len(ayahs) else len(words)
        chunk = words[s:e]

        if chunk:
            ayah["start_ms"]   = chunk[0]["start_ms"]
            ayah["end_ms"]     = chunk[-1]["end_ms"]
            ayah["confidence"] = round(
                sum(w["confidence"] for w in chunk) / len(chunk), 4
            )
        ayah["needs_review"] = ayah.get("confidence", 0) < 0.75

        print(
            f"  Ayah {ayah['ayah']:>2}: {ayah['start_ms']:>6} ms – {ayah['end_ms']:>6} ms"
            f"  ({_word_count(ayah['text_uthmani'])} canon words, {len(chunk)} speech words,"
            f"  conf={ayah['confidence']:.3f})"
        )

    # Last ayah always extends to end of audio
    if ayahs:
        ayahs[-1]["end_ms"] = audio_end_ms

    # Close any gaps between consecutive ayahs
    for i in range(1, len(ayahs)):
        prev, curr = ayahs[i - 1], ayahs[i]
        if curr["start_ms"] > prev["end_ms"]:
            mid = (prev["end_ms"] + curr["start_ms"]) // 2
            prev["end_ms"]    = mid
            curr["start_ms"]  = mid

    with open(align_path, "w", encoding="utf-8") as f:
        json.dump(align, f, ensure_ascii=False, indent=2)

    print(f"  ✓ Saved {align_path.name}\n")


if __name__ == "__main__":
    # Surah 1 (Al-Fatiha): Basmala IS ayah 1 — no fix needed
    # Surah 9 (At-Tawbah): no Basmala — no fix needed
    for s in [112, 114]:
        print(f"\n{'='*50}")
        fix_surah(s, "mishary_alafasy")
    print("All done.")
