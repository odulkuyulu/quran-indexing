"""
Align Module - Forced Alignment with WhisperX

Performs forced alignment of canonical Uthmani text against audio to produce
word-level timestamps. The text is NEVER generated — only timestamps are extracted.

Approach:
1. Run Whisper transcription to get initial word timings (raw ASR)
2. Use wav2vec2 to refine word boundaries
3. Map ASR words to canonical text words using sequence alignment
4. Collapse word timestamps into ayah spans (start_ms, end_ms)

The canonical text is the OUTPUT — ASR text is discarded after alignment.
"""

import os
import re
import warnings
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional, Tuple, Dict, Any

import torch
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

# Suppress warnings during import
warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

console = Console()


@dataclass
class WordTiming:
    """A single word with timing information."""
    word: str
    start_ms: int
    end_ms: int
    confidence: float


@dataclass
class AyahTiming:
    """Timing information for a complete ayah."""
    surah: int
    ayah: int
    text_uthmani: str  # Canonical text (INPUT, not generated)
    start_ms: int
    end_ms: int
    word_timings: List[WordTiming] = field(default_factory=list)
    # ASR comparison for scoring
    asr_text: Optional[str] = None
    alignment_confidence: float = 0.0


@dataclass
class AlignmentResult:
    """Complete alignment result for a surah."""
    surah: int
    ayah_timings: List[AyahTiming]
    audio_duration_ms: int
    model_used: str
    device: str
    # Metrics
    total_words_aligned: int = 0
    mean_word_confidence: float = 0.0


def get_device() -> str:
    """Determine best available compute device."""
    if torch.cuda.is_available():
        return "cuda"
    elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def normalize_arabic_for_alignment(text: str) -> str:
    """
    Normalize Arabic text for alignment comparison.
    
    Removes diacritics/tashkeel to improve word matching between
    ASR output and canonical Uthmani text.
    """
    # Arabic diacritics (tashkeel)
    tashkeel = re.compile(r'[\u064B-\u065F\u0670]')
    # Tatweel (kashida)
    tatweel = re.compile(r'\u0640')
    # Normalize alef variants
    alef_variants = re.compile(r'[إأآا]')
    
    text = tashkeel.sub('', text)
    text = tatweel.sub('', text)
    text = alef_variants.sub('ا', text)
    
    return text.strip()


def tokenize_arabic(text: str) -> List[str]:
    """Split Arabic text into words."""
    # Simple whitespace tokenization
    words = text.split()
    # Filter empty strings
    return [w.strip() for w in words if w.strip()]


class WhisperXAligner:
    """
    Forced alignment using WhisperX.
    
    WhisperX provides word-level timestamps through:
    1. Whisper transcription with word timings
    2. wav2vec2 forced alignment for refinement
    """
    
    def __init__(
        self,
        model_size: str = "large-v2",
        device: Optional[str] = None,
        compute_type: str = "float16"
    ):
        self.model_size = model_size
        self.device = device or get_device()
        self.compute_type = compute_type if self.device == "cuda" else "int8"
        
        self.model = None
        self.align_model = None
        self.align_metadata = None
        
        console.print(f"[dim]Using device: {self.device}[/dim]")
    
    def _load_models(self):
        """Lazy-load models on first use."""
        if self.model is not None:
            return
        
        console.print(f"[blue]Loading Whisper model: {self.model_size}[/blue]")
        
        try:
            import whisperx
        except ImportError:
            raise ImportError(
                "whisperx not installed. Install with:\n"
                "pip install git+https://github.com/m-bain/whisperX.git"
            )
        
        # Load Whisper model
        self.model = whisperx.load_model(
            self.model_size,
            self.device,
            compute_type=self.compute_type,
            language="ar"
        )
        
        console.print("[blue]Loading alignment model (wav2vec2)[/blue]")
        
        # Load alignment model for Arabic
        self.align_model, self.align_metadata = whisperx.load_align_model(
            language_code="ar",
            device=self.device
        )
        
        console.print("[green]✓[/green] Models loaded")
    
    def transcribe_with_timings(
        self,
        audio_path: Path,
        batch_size: int = 16
    ) -> Dict[str, Any]:
        """
        Transcribe audio and get word-level timings.
        
        This is the RAW ASR pass — output text is NOT used as canonical text.
        We only use the word timings.
        """
        import whisperx
        
        self._load_models()
        
        console.print(f"[blue]Transcribing:[/blue] {audio_path.name}")
        
        # Load audio
        audio = whisperx.load_audio(str(audio_path))
        
        # Transcribe
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("Running Whisper transcription...", total=None)
            
            result = self.model.transcribe(
                audio,
                batch_size=batch_size,
                language="ar"
            )
        
        console.print("[blue]Aligning with wav2vec2...[/blue]")
        
        # Align to get word-level timings
        result = whisperx.align(
            result["segments"],
            self.align_model,
            self.align_metadata,
            audio,
            self.device,
            return_char_alignments=False
        )
        
        return result
    
    def extract_word_timings(self, whisperx_result: Dict) -> List[WordTiming]:
        """Extract word timings from WhisperX result."""
        timings = []
        
        for segment in whisperx_result.get("segments", []):
            for word_info in segment.get("words", []):
                word = word_info.get("word", "").strip()
                if not word:
                    continue
                
                timings.append(WordTiming(
                    word=word,
                    start_ms=int(word_info.get("start", 0) * 1000),
                    end_ms=int(word_info.get("end", 0) * 1000),
                    confidence=word_info.get("score", 0.0)
                ))
        
        return timings


def align_words_to_ayahs(
    word_timings: List[WordTiming],
    ayahs: List["Ayah"],  # From text_corpus
    audio_duration_ms: int
) -> List[AyahTiming]:
    """
    Map word timings to ayah boundaries.
    
    Strategy:
    1. Tokenize canonical text for each ayah
    2. Sequentially consume word timings, assigning to ayahs
    3. Use DTW or greedy matching to handle ASR errors
    
    The canonical text is PRESERVED — we only use ASR for timing, not content.
    """
    ayah_timings = []
    word_idx = 0
    
    for ayah in ayahs:
        # Tokenize canonical text
        canonical_words = tokenize_arabic(ayah.text_uthmani)
        expected_word_count = len(canonical_words)
        
        if expected_word_count == 0:
            # Empty ayah (shouldn't happen)
            continue
        
        # Collect word timings for this ayah
        ayah_word_timings = []
        asr_words = []
        
        # Greedy: consume roughly expected_word_count words
        # Allow some flexibility for ASR errors
        words_to_consume = min(
            expected_word_count + 2,  # Allow 2 extra for ASR splits
            len(word_timings) - word_idx
        )
        
        # Simple approach: consume words until we hit next ayah boundary
        # or run out of words
        consumed = 0
        while word_idx < len(word_timings) and consumed < words_to_consume:
            wt = word_timings[word_idx]
            ayah_word_timings.append(wt)
            asr_words.append(wt.word)
            word_idx += 1
            consumed += 1
        
        # Compute ayah boundaries
        if ayah_word_timings:
            start_ms = ayah_word_timings[0].start_ms
            end_ms = ayah_word_timings[-1].end_ms
            mean_conf = sum(w.confidence for w in ayah_word_timings) / len(ayah_word_timings)
        else:
            # Fallback: estimate based on position
            idx = ayah.ayah - 1
            total_ayahs = len(ayahs)
            start_ms = int(audio_duration_ms * idx / total_ayahs)
            end_ms = int(audio_duration_ms * (idx + 1) / total_ayahs)
            mean_conf = 0.0
        
        ayah_timings.append(AyahTiming(
            surah=ayah.surah,
            ayah=ayah.ayah,
            text_uthmani=ayah.text_uthmani,  # Canonical text preserved
            start_ms=start_ms,
            end_ms=end_ms,
            word_timings=ayah_word_timings,
            asr_text=" ".join(asr_words),
            alignment_confidence=mean_conf
        ))
    
    return ayah_timings


def refine_ayah_boundaries(
    ayah_timings: List[AyahTiming],
    audio_duration_ms: int
) -> List[AyahTiming]:
    """
    Post-process ayah boundaries to remove gaps and overlaps.
    
    Rules:
    1. First ayah starts at 0 (or near 0)
    2. Last ayah ends at audio_duration_ms
    3. Adjacent ayahs share boundaries (no gaps)
    4. start_ms < end_ms for all ayahs
    """
    if not ayah_timings:
        return ayah_timings
    
    # Sort by ayah number
    ayah_timings = sorted(ayah_timings, key=lambda a: a.ayah)
    
    # Adjust first ayah to start near beginning
    first = ayah_timings[0]
    if first.start_ms > 1000:  # More than 1 second gap
        # Could be bismillah, keep as-is
        pass
    else:
        first.start_ms = 0
    
    # Ensure last ayah extends to end
    last = ayah_timings[-1]
    if audio_duration_ms - last.end_ms > 1000:
        # Extend to end
        last.end_ms = audio_duration_ms
    
    # Remove gaps between ayahs
    for i in range(1, len(ayah_timings)):
        prev = ayah_timings[i - 1]
        curr = ayah_timings[i]
        
        if curr.start_ms > prev.end_ms:
            # Gap: split the difference
            mid = (prev.end_ms + curr.start_ms) // 2
            prev.end_ms = mid
            curr.start_ms = mid
        elif curr.start_ms < prev.end_ms:
            # Overlap: use previous end as current start
            curr.start_ms = prev.end_ms
    
    # Ensure start < end for all
    for timing in ayah_timings:
        if timing.start_ms >= timing.end_ms:
            timing.end_ms = timing.start_ms + 100  # Minimum 100ms duration
    
    return ayah_timings


def run_forced_alignment(
    audio_path: Path,
    surah_text: "SurahText",  # From text_corpus
    audio_duration_ms: int,
    model_size: str = "large-v2",
    device: Optional[str] = None
) -> AlignmentResult:
    """
    Run complete forced alignment pipeline.
    
    CRITICAL: The canonical text in surah_text is NEVER modified.
    We only extract TIMING information from the audio.
    
    Args:
        audio_path: Path to normalized WAV file
        surah_text: Canonical text from text_corpus (alignment target)
        audio_duration_ms: Audio duration in milliseconds
        model_size: Whisper model size
        device: Compute device (cuda/cpu)
        
    Returns:
        AlignmentResult with per-ayah timings
    """
    console.print(f"\n[bold]═══ ALIGN: Surah {surah_text.surah} ═══[/bold]\n")
    
    device = device or get_device()
    
    # Initialize aligner
    aligner = WhisperXAligner(model_size=model_size, device=device)
    
    # Run transcription with timings
    whisperx_result = aligner.transcribe_with_timings(audio_path)
    
    # Extract word timings
    word_timings = aligner.extract_word_timings(whisperx_result)
    
    console.print(f"[dim]Extracted {len(word_timings)} word timings[/dim]")
    
    # Map to ayahs
    ayah_timings = align_words_to_ayahs(
        word_timings,
        surah_text.ayahs,
        audio_duration_ms
    )
    
    # Refine boundaries
    ayah_timings = refine_ayah_boundaries(ayah_timings, audio_duration_ms)
    
    # Compute overall metrics
    total_words = sum(len(at.word_timings) for at in ayah_timings)
    all_confidences = [
        w.confidence for at in ayah_timings for w in at.word_timings
    ]
    mean_conf = sum(all_confidences) / len(all_confidences) if all_confidences else 0.0
    
    console.print(f"[green]✓[/green] Aligned {len(ayah_timings)} ayahs")
    console.print(f"[dim]Total words: {total_words}, Mean confidence: {mean_conf:.3f}[/dim]")
    
    return AlignmentResult(
        surah=surah_text.surah,
        ayah_timings=ayah_timings,
        audio_duration_ms=audio_duration_ms,
        model_used=f"whisperx-{model_size}",
        device=device,
        total_words_aligned=total_words,
        mean_word_confidence=mean_conf
    )


if __name__ == "__main__":
    # Test alignment with mock data
    from text_corpus import load_surah_text, Ayah
    from ingest import AudioMetadata
    from dotenv import load_dotenv
    
    load_dotenv()
    
    console.print("[yellow]Alignment module loaded successfully[/yellow]")
    console.print("[dim]Run full pipeline to test alignment[/dim]")
