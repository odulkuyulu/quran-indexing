"""
Main Pipeline - Quran Audio-to-Ayah Indexer

Orchestrates the complete pipeline:
1. INGEST: Download and normalize audio
2. LOAD TEXT: Fetch canonical Uthmani text
3. ALIGN: Forced alignment with WhisperX
4. SCORE: Compute confidence metrics
5. EMIT: Write JSON index
6. (Optional) LOAD: Upload to Azure AI Search

Usage:
    python -m src.pipeline --surah 67 --reciter Mishary_Alafasy
"""

import os
import sys
import argparse
import time
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Load environment variables
load_dotenv()

console = Console()


@dataclass
class PipelineConfig:
    """Configuration for a pipeline run."""
    surah: int
    reciter: str
    qiraa: str = "Hafs"
    # Audio source
    audio_source_pattern: str = ""
    # Directories
    audio_dir: Path = Path("./data/audio")
    corpus_dir: Path = Path("./data/corpus")
    output_dir: Path = Path("./data/output")
    # Corpus source
    corpus_source: str = "qurancom"
    # Alignment
    whisper_model: str = "large-v2"
    compute_device: str = "auto"
    # Scoring
    confidence_threshold: float = 0.85
    # Azure AI Search (optional)
    upload_to_search: bool = False
    
    @classmethod
    def from_env(cls, surah: int, reciter: str, **overrides):
        """Create config from environment variables with overrides."""
        defaults = dict(
            surah=surah,
            reciter=reciter,
            audio_source_pattern=os.getenv(
                "AUDIO_SOURCE_PATTERN",
                "https://server8.mp3quran.net/{reciter}/{surah:03d}.mp3"
            ),
            audio_dir=Path(os.getenv("AUDIO_DIR", "./data/audio")),
            corpus_dir=Path(os.getenv("CORPUS_DIR", "./data/corpus")),
            output_dir=Path(os.getenv("OUTPUT_DIR", "./data/output")),
            corpus_source=os.getenv("CORPUS_SOURCE", "qurancom"),
            whisper_model=os.getenv("WHISPER_MODEL", "large-v2"),
            compute_device=os.getenv("COMPUTE_DEVICE", "auto"),
            confidence_threshold=float(os.getenv("CONFIDENCE_THRESHOLD", "0.85")),
            upload_to_search=bool(os.getenv("AZURE_SEARCH_ENDPOINT")),
        )
        defaults.update(overrides)
        return cls(**defaults)


@dataclass
class PipelineResult:
    """Result of a pipeline run."""
    success: bool
    surah: int
    reciter: str
    output_path: Optional[Path]
    ayah_count: int
    mean_confidence: float
    ayahs_needing_review: int
    duration_seconds: float
    error: Optional[str] = None


def run_pipeline(config: PipelineConfig) -> PipelineResult:
    """
    Run the complete indexing pipeline.
    
    CRITICAL: The canonical Uthmani text is NEVER generated or modified.
    It is loaded from the corpus and passed through unchanged.
    """
    start_time = time.time()
    
    console.print(Panel(
        f"[bold cyan]Quran Audio-to-Ayah Indexer[/bold cyan]\n\n"
        f"Surah: {config.surah}\n"
        f"Reciter: {config.reciter}\n"
        f"Model: {config.whisper_model}",
        title="Pipeline Start"
    ))
    
    try:
        # Stage 1: INGEST
        from .ingest import ingest_surah
        
        audio_metadata = ingest_surah(
            surah=config.surah,
            reciter=config.reciter,
            audio_source_pattern=config.audio_source_pattern,
            audio_dir=config.audio_dir
        )
        
        # Stage 2: LOAD TEXT
        from .text_corpus import load_surah_text, compute_text_hash
        
        surah_text = load_surah_text(
            surah=config.surah,
            corpus_dir=config.corpus_dir,
            source=config.corpus_source
        )
        
        # Record text hash for integrity verification
        text_hash = compute_text_hash(surah_text)
        console.print(f"[dim]Text hash: {text_hash}[/dim]")
        
        # Stage 3: ALIGN
        from .align import run_forced_alignment
        
        alignment_result = run_forced_alignment(
            audio_path=audio_metadata.file_path,
            surah_text=surah_text,
            audio_duration_ms=audio_metadata.duration_ms,
            model_size=config.whisper_model,
            device=config.compute_device if config.compute_device != "auto" else None
        )
        
        # Stage 4: SCORE
        from .score import score_alignment
        
        surah_score = score_alignment(
            alignment_result=alignment_result,
            confidence_threshold=config.confidence_threshold
        )
        
        # Stage 5: EMIT
        from .emit import emit_json
        
        output_path = emit_json(
            alignment_result=alignment_result,
            surah_score=surah_score,
            original_text=surah_text,
            reciter=config.reciter,
            qiraa=config.qiraa,
            audio_url=audio_metadata.source_url,
            output_dir=config.output_dir
        )
        
        # Stage 6: LOAD (optional)
        if config.upload_to_search:
            from .search_loader import upload_index_to_search
            upload_index_to_search(output_path)
        
        duration = time.time() - start_time
        
        # Print final summary
        _print_final_summary(
            config=config,
            audio_metadata=audio_metadata,
            surah_score=surah_score,
            output_path=output_path,
            duration=duration
        )
        
        return PipelineResult(
            success=True,
            surah=config.surah,
            reciter=config.reciter,
            output_path=output_path,
            ayah_count=len(alignment_result.ayah_timings),
            mean_confidence=surah_score.mean_confidence,
            ayahs_needing_review=surah_score.ayahs_needing_review,
            duration_seconds=duration
        )
        
    except Exception as e:
        duration = time.time() - start_time
        console.print(f"[red]Pipeline failed: {e}[/red]")
        
        import traceback
        traceback.print_exc()
        
        return PipelineResult(
            success=False,
            surah=config.surah,
            reciter=config.reciter,
            output_path=None,
            ayah_count=0,
            mean_confidence=0.0,
            ayahs_needing_review=0,
            duration_seconds=duration,
            error=str(e)
        )


def _print_final_summary(
    config: PipelineConfig,
    audio_metadata,
    surah_score,
    output_path: Path,
    duration: float
):
    """Print final pipeline summary."""
    table = Table(title="Pipeline Complete ✓")
    
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    
    table.add_row("Surah", str(config.surah))
    table.add_row("Reciter", config.reciter)
    table.add_row("Audio Duration", f"{audio_metadata.duration_ms / 1000:.1f}s")
    table.add_row("Ayahs Processed", str(len(surah_score.ayah_scores)))
    table.add_row("Mean Confidence", f"{surah_score.mean_confidence:.3f}")
    table.add_row("Needs Review", str(surah_score.ayahs_needing_review))
    table.add_row("Output File", str(output_path))
    table.add_row("Pipeline Duration", f"{duration:.1f}s")
    
    console.print("\n")
    console.print(table)
    
    # Cost note
    console.print("\n[bold]Cost Analysis:[/bold]")
    console.print(
        f"  • Compute time: {duration:.1f}s for {audio_metadata.duration_ms / 1000:.1f}s audio "
        f"({duration / (audio_metadata.duration_ms / 1000):.2f}x real-time)"
    )
    console.print("  • Text corpus API: Free (Quran.com API)")
    console.print("  • Total cost: One-time compute only, no per-query fees")
    
    console.print("\n[dim]Canonical text was preserved verbatim — no hallucination possible.[/dim]")


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Quran Audio-to-Ayah Indexer - Forced Alignment Pipeline"
    )
    
    parser.add_argument(
        "--surah", "-s",
        type=int,
        required=True,
        help="Surah number (1-114)"
    )
    
    parser.add_argument(
        "--reciter", "-r",
        type=str,
        default=os.getenv("DEFAULT_RECITER", "mishary_alafasy"),
        help="Reciter identifier (default: mishary_alafasy)"
    )
    
    parser.add_argument(
        "--model",
        type=str,
        default=os.getenv("WHISPER_MODEL", "large-v2"),
        help="Whisper model size (default: large-v2)"
    )
    
    parser.add_argument(
        "--device",
        type=str,
        default="auto",
        choices=["auto", "cuda", "cpu"],
        help="Compute device (default: auto)"
    )
    
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload to Azure AI Search"
    )
    
    parser.add_argument(
        "--output-dir",
        type=str,
        default=os.getenv("OUTPUT_DIR", "./data/output"),
        help="Output directory for JSON files"
    )
    
    args = parser.parse_args()
    
    # Validate surah
    if not 1 <= args.surah <= 114:
        console.print(f"[red]Invalid surah number: {args.surah}. Must be 1-114.[/red]")
        sys.exit(1)
    
    # Create config
    config = PipelineConfig.from_env(
        surah=args.surah,
        reciter=args.reciter,
        whisper_model=args.model,
        compute_device=args.device,
        output_dir=Path(args.output_dir),
        upload_to_search=args.upload
    )
    
    # Run pipeline
    result = run_pipeline(config)
    
    if not result.success:
        sys.exit(1)


if __name__ == "__main__":
    main()
