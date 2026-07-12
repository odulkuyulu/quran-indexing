"""
Ingest Module - Audio Download and Normalization

Downloads full-surah recitation audio and normalizes to 16kHz mono WAV
for alignment processing. Uses ffprobe for accurate duration extraction.
"""

import os
import json
import subprocess
import tempfile
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import requests
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


@dataclass
class AudioMetadata:
    """Metadata extracted from audio file."""
    duration_ms: int
    sample_rate: int
    channels: int
    codec: str
    file_path: Path
    source_url: str


def get_ffprobe_path() -> str:
    """Get ffprobe executable path."""
    # Try common locations
    for path in ["ffprobe", "ffprobe.exe", r"C:\ffmpeg\bin\ffprobe.exe"]:
        try:
            subprocess.run([path, "-version"], capture_output=True, check=True)
            return path
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    raise RuntimeError(
        "ffprobe not found. Install FFmpeg: https://ffmpeg.org/download.html"
    )


def get_ffmpeg_path() -> str:
    """Get ffmpeg executable path."""
    for path in ["ffmpeg", "ffmpeg.exe", r"C:\ffmpeg\bin\ffmpeg.exe"]:
        try:
            subprocess.run([path, "-version"], capture_output=True, check=True)
            return path
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    raise RuntimeError(
        "ffmpeg not found. Install FFmpeg: https://ffmpeg.org/download.html"
    )


def extract_duration_ffprobe(file_path: Path) -> dict:
    """
    Extract accurate audio metadata using ffprobe.
    
    Do NOT trust API metadata — it may be absent or incorrect.
    ffprobe reads the actual file to get true duration.
    """
    ffprobe = get_ffprobe_path()
    
    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    probe_data = json.loads(result.stdout)
    
    # Find audio stream
    audio_stream = None
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "audio":
            audio_stream = stream
            break
    
    if not audio_stream:
        raise ValueError(f"No audio stream found in {file_path}")
    
    # Get duration from format (more reliable) or stream
    format_info = probe_data.get("format", {})
    duration_seconds = float(
        format_info.get("duration") or audio_stream.get("duration", 0)
    )
    
    return {
        "duration_ms": int(duration_seconds * 1000),
        "sample_rate": int(audio_stream.get("sample_rate", 0)),
        "channels": int(audio_stream.get("channels", 0)),
        "codec": audio_stream.get("codec_name", "unknown"),
    }


def download_audio(
    url: str,
    output_dir: Path,
    filename: Optional[str] = None
) -> Path:
    """
    Download audio file from URL.
    
    Args:
        url: Source URL for the audio file
        output_dir: Directory to save the file
        filename: Optional filename, defaults to URL basename
        
    Returns:
        Path to downloaded file
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if filename is None:
        filename = url.split("/")[-1]
    
    output_path = output_dir / filename
    
    # Skip if already downloaded
    if output_path.exists():
        console.print(f"[dim]Audio already exists: {output_path}[/dim]")
        return output_path
    
    console.print(f"[blue]Downloading:[/blue] {url}")
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("Downloading...", total=None)
        
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        
        total_size = int(response.headers.get("content-length", 0))
        
        with open(output_path, "wb") as f:
            downloaded = 0
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total_size:
                    progress.update(
                        task,
                        description=f"Downloaded {downloaded / 1024 / 1024:.1f} MB"
                    )
    
    console.print(f"[green]✓[/green] Saved to {output_path}")
    return output_path


def normalize_audio(
    input_path: Path,
    output_dir: Path,
    sample_rate: int = 16000,
    channels: int = 1
) -> Path:
    """
    Normalize audio to standard format for alignment.
    
    Converts to 16kHz mono WAV (required by most alignment models).
    
    Args:
        input_path: Source audio file
        output_dir: Directory for normalized output
        sample_rate: Target sample rate (default 16000)
        channels: Target channel count (default 1 = mono)
        
    Returns:
        Path to normalized WAV file
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_filename = input_path.stem + "_normalized.wav"
    output_path = output_dir / output_filename
    
    # Skip if already normalized
    if output_path.exists():
        # Verify it's the right format
        metadata = extract_duration_ffprobe(output_path)
        if metadata["sample_rate"] == sample_rate and metadata["channels"] == channels:
            console.print(f"[dim]Normalized audio exists: {output_path}[/dim]")
            return output_path
    
    console.print(f"[blue]Normalizing:[/blue] {input_path.name} → 16kHz mono WAV")
    
    ffmpeg = get_ffmpeg_path()
    
    cmd = [
        ffmpeg,
        "-y",  # Overwrite output
        "-i", str(input_path),
        "-ar", str(sample_rate),  # Sample rate
        "-ac", str(channels),      # Channels
        "-c:a", "pcm_s16le",       # 16-bit PCM
        str(output_path)
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode != 0:
        raise RuntimeError(f"FFmpeg normalization failed: {result.stderr}")
    
    console.print(f"[green]✓[/green] Normalized to {output_path}")
    return output_path


def ingest_surah(
    surah: int,
    reciter: str,
    audio_source_pattern: str,
    audio_dir: Path
) -> AudioMetadata:
    """
    Full ingest pipeline for a single surah.
    
    1. Download the full surah audio
    2. Extract true duration with ffprobe
    3. Normalize to 16kHz mono WAV
    
    Args:
        surah: Surah number (1-114)
        reciter: Reciter identifier (e.g., "mishary_alafasy")
        audio_source_pattern: URL pattern with {surah:03d} and {reciter} placeholders
        audio_dir: Base directory for audio files
        
    Returns:
        AudioMetadata with duration and file paths
    """
    if not 1 <= surah <= 114:
        raise ValueError(f"Invalid surah number: {surah}. Must be 1-114.")
    
    console.print(f"\n[bold]═══ INGEST: Surah {surah} by {reciter} ═══[/bold]\n")
    
    # Build URL from pattern
    url = audio_source_pattern.format(surah=surah, reciter=reciter)
    
    # Create directories
    raw_dir = audio_dir / "raw"
    normalized_dir = audio_dir / "normalized"
    
    # Download
    raw_path = download_audio(url, raw_dir, f"{surah:03d}_{reciter}.mp3")
    
    # Extract metadata from raw file
    raw_metadata = extract_duration_ffprobe(raw_path)
    console.print(
        f"[dim]Raw audio: {raw_metadata['duration_ms'] / 1000:.1f}s, "
        f"{raw_metadata['sample_rate']}Hz, {raw_metadata['channels']}ch[/dim]"
    )
    
    # Normalize
    normalized_path = normalize_audio(raw_path, normalized_dir)
    
    # Get final metadata
    final_metadata = extract_duration_ffprobe(normalized_path)
    
    return AudioMetadata(
        duration_ms=final_metadata["duration_ms"],
        sample_rate=final_metadata["sample_rate"],
        channels=final_metadata["channels"],
        codec="pcm_s16le",
        file_path=normalized_path,
        source_url=url
    )


if __name__ == "__main__":
    # Quick test
    from dotenv import load_dotenv
    load_dotenv()
    
    pattern = os.getenv(
        "AUDIO_SOURCE_PATTERN",
        "https://server8.mp3quran.net/{reciter}/{surah:03d}.mp3"
    )
    reciter = os.getenv("DEFAULT_RECITER", "mishary_alafasy")
    audio_dir = Path(os.getenv("AUDIO_DIR", "./data/audio"))
    
    # Test with Al-Fatiha (short)
    metadata = ingest_surah(1, reciter, pattern, audio_dir)
    console.print(f"\n[green]Result:[/green] {metadata}")
