"""
Emit Module - JSON Output Writer

Writes the final per-ayah index to JSON format.
CRITICAL: The canonical Uthmani text must match the corpus byte-for-byte.
"""

import json
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import List, Optional

from rich.console import Console

from .align import AlignmentResult, AyahTiming
from .score import SurahScore, AyahScore
from .text_corpus import compute_text_hash, assert_text_unchanged, SurahText

console = Console()


@dataclass
class AyahRecord:
    """
    Final output record for a single ayah.
    
    This is the schema for the JSON output.
    """
    surah: int
    ayah: int
    reciter: str
    qiraa: str
    audio_url: str
    start_ms: int
    end_ms: int
    text_uthmani: str  # Canonical text, verbatim
    confidence: float
    needs_review: bool


@dataclass
class SurahIndex:
    """Complete index for a surah."""
    surah: int
    name_arabic: str
    name_english: str
    reciter: str
    qiraa: str
    source_audio_url: str
    audio_duration_ms: int
    ayah_count: int
    ayahs: List[AyahRecord]
    # Metadata
    generated_at: str
    pipeline_version: str
    text_hash: str  # For integrity verification
    model_used: str
    mean_confidence: float
    ayahs_needing_review: int


def validate_output_integrity(
    ayah_records: List[AyahRecord],
    original_text: SurahText
) -> bool:
    """
    Verify that output text matches canonical corpus byte-for-byte.
    
    This is a CRITICAL check. The text must NEVER be modified by any model.
    """
    for record in ayah_records:
        original_ayah = next(
            (a for a in original_text.ayahs if a.ayah == record.ayah),
            None
        )
        if original_ayah is None:
            console.print(f"[red]ERROR: Ayah {record.ayah} not found in corpus[/red]")
            return False
        
        if record.text_uthmani != original_ayah.text_uthmani:
            console.print(
                f"[red]ERROR: Text mismatch for ayah {record.ayah}![/red]\n"
                f"Expected: {original_ayah.text_uthmani[:50]}...\n"
                f"Got: {record.text_uthmani[:50]}..."
            )
            return False
    
    return True


def build_ayah_records(
    alignment_result: AlignmentResult,
    surah_score: SurahScore,
    reciter: str,
    qiraa: str,
    audio_url: str
) -> List[AyahRecord]:
    """
    Build final ayah records from alignment and scoring results.
    
    The canonical text is copied directly from alignment_result.ayah_timings,
    which in turn came from the text_corpus (never generated).
    """
    records = []
    
    # Create lookup for scores
    score_map = {s.ayah: s for s in surah_score.ayah_scores}
    
    for timing in alignment_result.ayah_timings:
        score = score_map.get(timing.ayah)
        
        records.append(AyahRecord(
            surah=timing.surah,
            ayah=timing.ayah,
            reciter=reciter,
            qiraa=qiraa,
            audio_url=audio_url,
            start_ms=timing.start_ms,
            end_ms=timing.end_ms,
            text_uthmani=timing.text_uthmani,  # Canonical text, verbatim
            confidence=score.confidence if score else 0.0,
            needs_review=score.needs_review if score else True
        ))
    
    # Sort by ayah number
    records.sort(key=lambda r: r.ayah)
    
    return records


def emit_json(
    alignment_result: AlignmentResult,
    surah_score: SurahScore,
    original_text: SurahText,
    reciter: str,
    qiraa: str,
    audio_url: str,
    output_dir: Path,
    pipeline_version: str = "0.1.0"
) -> Path:
    """
    Write the final per-ayah index to JSON.
    
    CRITICAL: Validates that canonical text is unchanged before writing.
    
    Args:
        alignment_result: Alignment with timings
        surah_score: Quality scores
        original_text: Original corpus text (for integrity check)
        reciter: Reciter name
        qiraa: Qira'a (e.g., "Hafs")
        audio_url: Source audio URL
        output_dir: Directory for output files
        pipeline_version: Version string
        
    Returns:
        Path to written JSON file
    """
    console.print(f"\n[bold]═══ EMIT: Surah {alignment_result.surah} ═══[/bold]\n")
    
    # Build records
    ayah_records = build_ayah_records(
        alignment_result,
        surah_score,
        reciter,
        qiraa,
        audio_url
    )
    
    # CRITICAL: Validate text integrity
    console.print("[blue]Validating text integrity...[/blue]")
    if not validate_output_integrity(ayah_records, original_text):
        raise AssertionError(
            "CRITICAL: Canonical text has been modified! "
            "This should never happen. Pipeline aborted."
        )
    console.print("[green]✓[/green] Text integrity verified")
    
    # Compute text hash
    text_hash = compute_text_hash(original_text)
    
    # Build index
    index = SurahIndex(
        surah=alignment_result.surah,
        name_arabic=original_text.name_arabic,
        name_english=original_text.name_english,
        reciter=reciter,
        qiraa=qiraa,
        source_audio_url=audio_url,
        audio_duration_ms=alignment_result.audio_duration_ms,
        ayah_count=len(ayah_records),
        ayahs=ayah_records,
        generated_at=datetime.utcnow().isoformat() + "Z",
        pipeline_version=pipeline_version,
        text_hash=text_hash,
        model_used=alignment_result.model_used,
        mean_confidence=surah_score.mean_confidence,
        ayahs_needing_review=surah_score.ayahs_needing_review
    )
    
    # Write JSON
    output_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{alignment_result.surah:03d}_{reciter}.json"
    output_path = output_dir / filename
    
    # Convert to dict with proper serialization
    index_dict = {
        "surah": index.surah,
        "name_arabic": index.name_arabic,
        "name_english": index.name_english,
        "reciter": index.reciter,
        "qiraa": index.qiraa,
        "source_audio_url": index.source_audio_url,
        "audio_duration_ms": index.audio_duration_ms,
        "ayah_count": index.ayah_count,
        "ayahs": [asdict(a) for a in index.ayahs],
        "metadata": {
            "generated_at": index.generated_at,
            "pipeline_version": index.pipeline_version,
            "text_hash": index.text_hash,
            "model_used": index.model_used,
            "mean_confidence": index.mean_confidence,
            "ayahs_needing_review": index.ayahs_needing_review
        }
    }
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(index_dict, f, ensure_ascii=False, indent=2)
    
    console.print(f"[green]✓[/green] Written to {output_path}")
    
    # Validate the output is parseable
    with open(output_path, "r", encoding="utf-8") as f:
        _ = json.load(f)
    
    # Summary
    console.print(f"\n[bold]Output Summary:[/bold]")
    console.print(f"  • Surah: {index.surah} ({index.name_english})")
    console.print(f"  • Ayahs: {index.ayah_count}")
    console.print(f"  • Duration: {index.audio_duration_ms / 1000:.1f}s")
    console.print(f"  • Mean confidence: {index.mean_confidence:.3f}")
    console.print(f"  • Needs review: {index.ayahs_needing_review}")
    console.print(f"  • Text hash: {text_hash}")
    
    return output_path


def validate_json_output(json_path: Path) -> dict:
    """
    Validate a previously written JSON output file.
    
    Checks:
    - Valid JSON syntax
    - All required fields present
    - start_ms < end_ms for all ayahs
    - Non-empty text for all ayahs
    - Ayah numbers are sequential
    """
    errors = []
    warnings = []
    
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return {"valid": False, "errors": [f"Invalid JSON: {e}"]}
    
    # Required top-level fields
    required_fields = ["surah", "ayahs", "ayah_count"]
    for field in required_fields:
        if field not in data:
            errors.append(f"Missing required field: {field}")
    
    if errors:
        return {"valid": False, "errors": errors}
    
    # Validate ayahs
    ayahs = data.get("ayahs", [])
    
    if len(ayahs) != data.get("ayah_count", 0):
        errors.append(
            f"Ayah count mismatch: expected {data.get('ayah_count')}, "
            f"got {len(ayahs)}"
        )
    
    prev_ayah = 0
    for i, ayah in enumerate(ayahs):
        # Check required ayah fields
        ayah_num = ayah.get("ayah", i + 1)
        
        if ayah.get("start_ms", 0) >= ayah.get("end_ms", 0):
            errors.append(
                f"Ayah {ayah_num}: start_ms ({ayah.get('start_ms')}) >= "
                f"end_ms ({ayah.get('end_ms')})"
            )
        
        text = ayah.get("text_uthmani", "")
        if not text or not text.strip():
            errors.append(f"Ayah {ayah_num}: empty text_uthmani")
        
        if ayah_num != prev_ayah + 1:
            warnings.append(f"Ayah numbering gap: {prev_ayah} -> {ayah_num}")
        prev_ayah = ayah_num
    
    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "ayah_count": len(ayahs),
        "text_hash": data.get("metadata", {}).get("text_hash")
    }


if __name__ == "__main__":
    console.print("[yellow]Emit module loaded successfully[/yellow]")
    console.print("[dim]Run full pipeline to test emission[/dim]")
