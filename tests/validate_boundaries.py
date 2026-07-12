"""
Boundary Validation Script

Validates alignment boundaries against ground-truth per-ayah clips
from EveryAyah.com. Computes mean and max boundary error.

Usage:
    python -m tests.validate_boundaries --surah 67 --reciter Mishary_Alafasy

Target: Mean boundary error < 300ms
"""

import os
import json
import argparse
import tempfile
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple, Optional

import httpx
from rich.console import Console
from rich.table import Table
from dotenv import load_dotenv

load_dotenv()

console = Console()


@dataclass
class BoundaryComparison:
    """Comparison between aligned and ground-truth boundaries."""
    ayah: int
    aligned_start_ms: int
    aligned_end_ms: int
    gt_start_ms: int
    gt_end_ms: int
    gt_duration_ms: int
    start_error_ms: int
    end_error_ms: int


def get_audio_duration_ffprobe(file_path: Path) -> int:
    """Get audio duration in milliseconds using ffprobe."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(file_path)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return 0
    
    data = json.loads(result.stdout)
    duration_s = float(data.get("format", {}).get("duration", 0))
    return int(duration_s * 1000)


def download_everyayah_clip(
    surah: int,
    ayah: int,
    reciter: str,
    output_dir: Path
) -> Optional[Path]:
    """
    Download per-ayah clip from EveryAyah.com.
    
    These clips serve as ground truth for boundary validation.
    """
    # EveryAyah URL pattern (reciter folder names vary)
    pattern = os.getenv(
        "EVERYAYAH_PATTERN",
        "https://everyayah.com/data/{reciter}/{surah:03d}{ayah:03d}.mp3"
    )
    
    url = pattern.format(surah=surah, ayah=ayah, reciter=reciter)
    
    output_path = output_dir / f"{surah:03d}{ayah:03d}.mp3"
    
    if output_path.exists():
        return output_path
    
    try:
        response = httpx.get(url, timeout=30.0, follow_redirects=True)
        if response.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(response.content)
            return output_path
    except Exception as e:
        console.print(f"[dim]Failed to download ayah {ayah}: {e}[/dim]")
    
    return None


def load_aligned_boundaries(json_path: Path) -> List[Tuple[int, int, int]]:
    """Load ayah boundaries from alignment JSON output."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    boundaries = []
    for ayah in data.get("ayahs", []):
        boundaries.append((
            ayah["ayah"],
            ayah["start_ms"],
            ayah["end_ms"]
        ))
    
    return boundaries


def compute_ground_truth_boundaries(
    surah: int,
    ayah_count: int,
    reciter: str,
    cache_dir: Path
) -> List[Tuple[int, int, int]]:
    """
    Compute ground truth boundaries from EveryAyah clips.
    
    Assumes clips are contiguous in the full surah audio.
    Returns (ayah, cumulative_start_ms, cumulative_end_ms).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    
    boundaries = []
    cumulative_ms = 0
    
    for ayah_num in range(1, ayah_count + 1):
        clip_path = download_everyayah_clip(surah, ayah_num, reciter, cache_dir)
        
        if clip_path:
            duration_ms = get_audio_duration_ffprobe(clip_path)
        else:
            # Estimate from average
            duration_ms = 3000  # Default 3s
        
        start_ms = cumulative_ms
        end_ms = cumulative_ms + duration_ms
        
        boundaries.append((ayah_num, start_ms, end_ms))
        cumulative_ms = end_ms
    
    return boundaries


def validate_boundaries(
    aligned: List[Tuple[int, int, int]],
    ground_truth: List[Tuple[int, int, int]]
) -> List[BoundaryComparison]:
    """Compare aligned boundaries against ground truth."""
    comparisons = []
    
    gt_map = {gt[0]: (gt[1], gt[2]) for gt in ground_truth}
    
    for ayah_num, aligned_start, aligned_end in aligned:
        if ayah_num not in gt_map:
            continue
        
        gt_start, gt_end = gt_map[ayah_num]
        
        comparisons.append(BoundaryComparison(
            ayah=ayah_num,
            aligned_start_ms=aligned_start,
            aligned_end_ms=aligned_end,
            gt_start_ms=gt_start,
            gt_end_ms=gt_end,
            gt_duration_ms=gt_end - gt_start,
            start_error_ms=abs(aligned_start - gt_start),
            end_error_ms=abs(aligned_end - gt_end)
        ))
    
    return comparisons


def print_validation_report(
    comparisons: List[BoundaryComparison],
    target_error_ms: int = 300
):
    """Print validation report with metrics."""
    if not comparisons:
        console.print("[yellow]No comparisons available[/yellow]")
        return
    
    start_errors = [c.start_error_ms for c in comparisons]
    end_errors = [c.end_error_ms for c in comparisons]
    
    mean_start = sum(start_errors) / len(start_errors)
    mean_end = sum(end_errors) / len(end_errors)
    max_start = max(start_errors)
    max_end = max(end_errors)
    
    # Summary table
    table = Table(title="Boundary Validation Results")
    table.add_column("Metric", style="cyan")
    table.add_column("Start", style="green")
    table.add_column("End", style="green")
    
    table.add_row("Mean Error (ms)", f"{mean_start:.0f}", f"{mean_end:.0f}")
    table.add_row("Max Error (ms)", f"{max_start}", f"{max_end}")
    table.add_row("Target (ms)", f"< {target_error_ms}", f"< {target_error_ms}")
    
    console.print(table)
    
    # Pass/Fail
    if mean_start < target_error_ms and mean_end < target_error_ms:
        console.print(f"\n[green]✓ PASS: Mean error < {target_error_ms}ms[/green]")
    else:
        console.print(f"\n[red]✗ FAIL: Mean error >= {target_error_ms}ms[/red]")
    
    # Detailed per-ayah table
    console.print("\n[bold]Per-Ayah Details:[/bold]")
    
    detail_table = Table()
    detail_table.add_column("Ayah", style="cyan", justify="right")
    detail_table.add_column("Aligned Start", justify="right")
    detail_table.add_column("GT Start", justify="right")
    detail_table.add_column("Start Δ", justify="right")
    detail_table.add_column("Aligned End", justify="right")
    detail_table.add_column("GT End", justify="right")
    detail_table.add_column("End Δ", justify="right")
    
    for c in comparisons[:20]:  # Show first 20
        start_style = "red" if c.start_error_ms > target_error_ms else "green"
        end_style = "red" if c.end_error_ms > target_error_ms else "green"
        
        detail_table.add_row(
            str(c.ayah),
            f"{c.aligned_start_ms / 1000:.2f}s",
            f"{c.gt_start_ms / 1000:.2f}s",
            f"[{start_style}]{c.start_error_ms}ms[/{start_style}]",
            f"{c.aligned_end_ms / 1000:.2f}s",
            f"{c.gt_end_ms / 1000:.2f}s",
            f"[{end_style}]{c.end_error_ms}ms[/{end_style}]"
        )
    
    if len(comparisons) > 20:
        detail_table.add_row("...", "...", "...", "...", "...", "...", "...")
    
    console.print(detail_table)


def main():
    parser = argparse.ArgumentParser(
        description="Validate alignment boundaries against EveryAyah ground truth"
    )
    
    parser.add_argument(
        "--surah", "-s",
        type=int,
        required=True,
        help="Surah number"
    )
    
    parser.add_argument(
        "--reciter", "-r",
        type=str,
        default="Alafasy_128kbps",  # EveryAyah folder name
        help="EveryAyah reciter folder name"
    )
    
    parser.add_argument(
        "--aligned-json",
        type=str,
        help="Path to aligned JSON output (default: data/output/{surah:03d}_*.json)"
    )
    
    parser.add_argument(
        "--target-error",
        type=int,
        default=300,
        help="Target mean error in milliseconds (default: 300)"
    )
    
    args = parser.parse_args()
    
    console.print(f"\n[bold]═══ Boundary Validation: Surah {args.surah} ═══[/bold]\n")
    
    # Find aligned JSON
    output_dir = Path(os.getenv("OUTPUT_DIR", "./data/output"))
    
    if args.aligned_json:
        json_path = Path(args.aligned_json)
    else:
        json_files = list(output_dir.glob(f"{args.surah:03d}_*.json"))
        if not json_files:
            console.print(f"[red]No aligned JSON found for surah {args.surah}[/red]")
            console.print(f"Run: python -m src.pipeline --surah {args.surah}")
            return
        json_path = json_files[0]
    
    console.print(f"[dim]Using aligned JSON: {json_path}[/dim]")
    
    # Load aligned boundaries
    aligned = load_aligned_boundaries(json_path)
    console.print(f"[dim]Loaded {len(aligned)} aligned ayahs[/dim]")
    
    # Get ground truth
    cache_dir = Path(tempfile.gettempdir()) / "everyayah_cache" / args.reciter
    console.print(f"[blue]Downloading ground truth clips from EveryAyah...[/blue]")
    
    ground_truth = compute_ground_truth_boundaries(
        surah=args.surah,
        ayah_count=len(aligned),
        reciter=args.reciter,
        cache_dir=cache_dir
    )
    
    # Validate
    comparisons = validate_boundaries(aligned, ground_truth)
    
    # Print report
    print_validation_report(comparisons, args.target_error)


if __name__ == "__main__":
    main()
