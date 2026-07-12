"""
Score Module - Confidence and Quality Metrics

Computes per-ayah confidence scores and overall quality metrics.
WER (Word Error Rate) is computed as a SANITY SIGNAL only — the canonical
text is authoritative and is never replaced by ASR output.

Metrics:
- Alignment confidence (from model)
- WER vs canonical text (sanity check)
- Boundary consistency (gaps/overlaps)
- Flagging for human review
"""

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from rich.console import Console
from rich.table import Table

try:
    from jiwer import wer, cer
except ImportError:
    wer = None
    cer = None

from .align import AyahTiming, AlignmentResult

console = Console()


@dataclass
class AyahScore:
    """Quality scores for a single ayah."""
    surah: int
    ayah: int
    # Alignment confidence (from model)
    alignment_confidence: float
    # WER between ASR and canonical (sanity signal)
    wer: Optional[float] = None
    # Character error rate
    cer: Optional[float] = None
    # Final composite confidence
    confidence: float = 0.0
    # Flag for human review
    needs_review: bool = False
    # Review reasons
    review_reasons: List[str] = field(default_factory=list)


@dataclass
class SurahScore:
    """Aggregate scores for a complete surah."""
    surah: int
    ayah_scores: List[AyahScore]
    # Aggregates
    mean_confidence: float = 0.0
    mean_wer: Optional[float] = None
    ayahs_needing_review: int = 0
    # Quality checks
    boundary_issues: int = 0
    coverage_percent: float = 0.0


def normalize_for_comparison(text: str) -> str:
    """
    Normalize Arabic text for WER/CER comparison.
    
    Removes diacritics and normalizes characters to reduce false errors.
    """
    # Remove tashkeel (diacritics)
    tashkeel = re.compile(r'[\u064B-\u065F\u0670]')
    text = tashkeel.sub('', text)
    
    # Remove tatweel
    text = re.sub(r'\u0640', '', text)
    
    # Normalize alef variants
    text = re.sub(r'[إأآا]', 'ا', text)
    
    # Normalize ya/alef maqsura
    text = re.sub(r'[ىي]', 'ي', text)
    
    # Normalize ta marbuta/ha
    text = re.sub(r'ة', 'ه', text)
    
    # Remove extra whitespace
    text = ' '.join(text.split())
    
    return text.strip()


def compute_wer(reference: str, hypothesis: str) -> Optional[float]:
    """
    Compute Word Error Rate between canonical (reference) and ASR (hypothesis).
    
    This is a SANITY SIGNAL only. High WER may indicate alignment issues,
    but the canonical text is always authoritative.
    """
    if wer is None:
        return None
    
    ref_norm = normalize_for_comparison(reference)
    hyp_norm = normalize_for_comparison(hypothesis)
    
    if not ref_norm or not hyp_norm:
        return None
    
    try:
        return wer(ref_norm, hyp_norm)
    except Exception:
        return None


def compute_cer(reference: str, hypothesis: str) -> Optional[float]:
    """Compute Character Error Rate."""
    if cer is None:
        return None
    
    ref_norm = normalize_for_comparison(reference)
    hyp_norm = normalize_for_comparison(hypothesis)
    
    if not ref_norm or not hyp_norm:
        return None
    
    try:
        return cer(ref_norm, hyp_norm)
    except Exception:
        return None


def score_ayah(
    ayah_timing: AyahTiming,
    confidence_threshold: float = 0.85
) -> AyahScore:
    """
    Compute quality scores for a single ayah.
    
    Args:
        ayah_timing: Timing information including ASR text
        confidence_threshold: Below this, flag for review
        
    Returns:
        AyahScore with confidence and review flags
    """
    review_reasons = []
    
    # Base confidence from alignment
    alignment_conf = ayah_timing.alignment_confidence
    
    # Compute WER if ASR text available
    wer_score = None
    cer_score = None
    if ayah_timing.asr_text:
        wer_score = compute_wer(ayah_timing.text_uthmani, ayah_timing.asr_text)
        cer_score = compute_cer(ayah_timing.text_uthmani, ayah_timing.asr_text)
    
    # Composite confidence
    # Weight: 70% alignment confidence, 30% WER-based
    if wer_score is not None:
        wer_confidence = max(0, 1 - wer_score)  # Convert WER to confidence
        confidence = 0.7 * alignment_conf + 0.3 * wer_confidence
    else:
        confidence = alignment_conf
    
    # Review flags
    needs_review = False
    
    if confidence < confidence_threshold:
        needs_review = True
        review_reasons.append(f"Low confidence: {confidence:.2f}")
    
    if wer_score is not None and wer_score > 0.5:
        needs_review = True
        review_reasons.append(f"High WER: {wer_score:.2f}")
    
    # Duration sanity check
    duration_ms = ayah_timing.end_ms - ayah_timing.start_ms
    word_count = len(ayah_timing.text_uthmani.split())
    
    if duration_ms < 500 and word_count > 3:
        needs_review = True
        review_reasons.append(f"Suspiciously short: {duration_ms}ms for {word_count} words")
    
    if duration_ms > 60000:  # 1 minute
        needs_review = True
        review_reasons.append(f"Suspiciously long: {duration_ms}ms")
    
    return AyahScore(
        surah=ayah_timing.surah,
        ayah=ayah_timing.ayah,
        alignment_confidence=alignment_conf,
        wer=wer_score,
        cer=cer_score,
        confidence=confidence,
        needs_review=needs_review,
        review_reasons=review_reasons
    )


def score_alignment(
    alignment_result: AlignmentResult,
    confidence_threshold: float = 0.85
) -> SurahScore:
    """
    Compute quality scores for complete alignment result.
    
    Args:
        alignment_result: Full alignment with all ayah timings
        confidence_threshold: Threshold for flagging review
        
    Returns:
        SurahScore with per-ayah and aggregate metrics
    """
    console.print(f"\n[bold]═══ SCORE: Surah {alignment_result.surah} ═══[/bold]\n")
    
    ayah_scores = []
    
    for ayah_timing in alignment_result.ayah_timings:
        score = score_ayah(ayah_timing, confidence_threshold)
        ayah_scores.append(score)
    
    # Aggregate metrics
    confidences = [s.confidence for s in ayah_scores]
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    
    wer_values = [s.wer for s in ayah_scores if s.wer is not None]
    mean_wer = sum(wer_values) / len(wer_values) if wer_values else None
    
    ayahs_needing_review = sum(1 for s in ayah_scores if s.needs_review)
    
    # Check boundary issues
    boundary_issues = 0
    prev_end = 0
    for ayah_timing in alignment_result.ayah_timings:
        if ayah_timing.start_ms < prev_end - 100:  # Overlap > 100ms
            boundary_issues += 1
        if ayah_timing.start_ms > prev_end + 1000:  # Gap > 1s
            boundary_issues += 1
        prev_end = ayah_timing.end_ms
    
    # Coverage
    total_aligned_ms = sum(
        at.end_ms - at.start_ms for at in alignment_result.ayah_timings
    )
    coverage = total_aligned_ms / alignment_result.audio_duration_ms * 100
    
    surah_score = SurahScore(
        surah=alignment_result.surah,
        ayah_scores=ayah_scores,
        mean_confidence=mean_confidence,
        mean_wer=mean_wer,
        ayahs_needing_review=ayahs_needing_review,
        boundary_issues=boundary_issues,
        coverage_percent=coverage
    )
    
    # Print summary
    _print_score_summary(surah_score)
    
    return surah_score


def _print_score_summary(surah_score: SurahScore):
    """Print a summary table of scores."""
    table = Table(title=f"Surah {surah_score.surah} Quality Scores")
    
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    
    table.add_row("Total Ayahs", str(len(surah_score.ayah_scores)))
    table.add_row("Mean Confidence", f"{surah_score.mean_confidence:.3f}")
    
    if surah_score.mean_wer is not None:
        table.add_row("Mean WER", f"{surah_score.mean_wer:.3f}")
    
    table.add_row("Coverage", f"{surah_score.coverage_percent:.1f}%")
    table.add_row("Boundary Issues", str(surah_score.boundary_issues))
    table.add_row(
        "Needs Review",
        f"{surah_score.ayahs_needing_review}/{len(surah_score.ayah_scores)}"
    )
    
    console.print(table)
    
    # Print ayahs needing review
    if surah_score.ayahs_needing_review > 0:
        console.print("\n[yellow]Ayahs flagged for review:[/yellow]")
        for score in surah_score.ayah_scores:
            if score.needs_review:
                reasons = ", ".join(score.review_reasons)
                console.print(f"  • Ayah {score.ayah}: {reasons}")


def validate_boundaries_against_ground_truth(
    ayah_timings: List[AyahTiming],
    ground_truth_boundaries: List[Tuple[int, int, int]]  # (ayah, start_ms, end_ms)
) -> dict:
    """
    Validate alignment boundaries against ground truth (e.g., EveryAyah clips).
    
    Returns mean and max boundary error in milliseconds.
    """
    if not ground_truth_boundaries:
        return {"error": "No ground truth provided"}
    
    start_errors = []
    end_errors = []
    
    gt_map = {gt[0]: (gt[1], gt[2]) for gt in ground_truth_boundaries}
    
    for ayah_timing in ayah_timings:
        if ayah_timing.ayah in gt_map:
            gt_start, gt_end = gt_map[ayah_timing.ayah]
            
            start_error = abs(ayah_timing.start_ms - gt_start)
            end_error = abs(ayah_timing.end_ms - gt_end)
            
            start_errors.append(start_error)
            end_errors.append(end_error)
    
    if not start_errors:
        return {"error": "No matching ayahs for comparison"}
    
    return {
        "mean_start_error_ms": sum(start_errors) / len(start_errors),
        "mean_end_error_ms": sum(end_errors) / len(end_errors),
        "max_start_error_ms": max(start_errors),
        "max_end_error_ms": max(end_errors),
        "ayahs_compared": len(start_errors)
    }


if __name__ == "__main__":
    console.print("[yellow]Score module loaded successfully[/yellow]")
    console.print("[dim]Run full pipeline to test scoring[/dim]")
