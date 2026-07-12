"""
Text Corpus Module - Canonical Uthmani Text Loader

Fetches and caches the canonical Uthmani Arabic text for Quran ayahs.
This text is the ALIGNMENT TARGET — it is never generated or modified by any model.

Sources:
- Quran.com API v4 (primary)
- Tanzil.net downloads (backup)
"""

import json
import hashlib
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any

import httpx
from rich.console import Console

console = Console()


@dataclass
class Ayah:
    """Represents a single ayah with its canonical text."""
    surah: int
    ayah: int
    text_uthmani: str
    # Optional: simple/clean text for alignment (no tashkeel)
    text_simple: Optional[str] = None
    
    def __post_init__(self):
        # Ensure text is not empty
        if not self.text_uthmani or not self.text_uthmani.strip():
            raise ValueError(f"Empty text for {self.surah}:{self.ayah}")


@dataclass 
class SurahText:
    """Complete text for a surah."""
    surah: int
    name_arabic: str
    name_english: str
    ayah_count: int
    ayahs: List[Ayah]
    source: str  # "qurancom" or "tanzil"
    
    def verify_integrity(self) -> bool:
        """Verify all ayahs are present and in order."""
        if len(self.ayahs) != self.ayah_count:
            return False
        for i, ayah in enumerate(self.ayahs, start=1):
            if ayah.ayah != i:
                return False
        return True


# Surah metadata (name, ayah count)
SURAH_METADATA = {
    1: ("الفاتحة", "Al-Fatiha", 7),
    2: ("البقرة", "Al-Baqara", 286),
    67: ("الملك", "Al-Mulk", 30),
    78: ("النبأ", "An-Naba", 40),
    112: ("الإخلاص", "Al-Ikhlas", 4),
    114: ("الناس", "An-Nas", 6),
    # Add more as needed, or fetch dynamically
}


class QuranComCorpus:
    """Fetch canonical text from Quran.com API v4."""
    
    BASE_URL = "https://api.quran.com/api/v4"
    
    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = httpx.Client(timeout=30.0)
    
    def _cache_path(self, surah: int) -> Path:
        return self.cache_dir / f"qurancom_surah_{surah:03d}.json"
    
    def _fetch_surah_metadata(self, surah: int) -> Dict[str, Any]:
        """Fetch surah metadata from API."""
        url = f"{self.BASE_URL}/chapters/{surah}"
        response = self.client.get(url)
        response.raise_for_status()
        return response.json()["chapter"]
    
    def _fetch_verses(self, surah: int) -> List[Dict[str, Any]]:
        """Fetch all verses for a surah with Uthmani text."""
        verses = []
        page = 1
        
        while True:
            url = f"{self.BASE_URL}/verses/by_chapter/{surah}"
            params = {
                "language": "ar",
                "words": "false",
                "page": page,
                "per_page": 50,
                # Request Uthmani text specifically
                "fields": "text_uthmani,text_imlaei"
            }
            
            response = self.client.get(url, params=params)
            response.raise_for_status()
            data = response.json()
            
            verses.extend(data["verses"])
            
            # Check pagination
            pagination = data.get("pagination", {})
            if page >= pagination.get("total_pages", 1):
                break
            page += 1
        
        return verses
    
    def load_surah(self, surah: int, force_refresh: bool = False) -> SurahText:
        """
        Load complete surah text from Quran.com API.
        
        Args:
            surah: Surah number (1-114)
            force_refresh: If True, bypass cache
            
        Returns:
            SurahText with all ayahs
        """
        cache_path = self._cache_path(surah)
        
        # Try cache first
        if not force_refresh and cache_path.exists():
            console.print(f"[dim]Loading cached text for Surah {surah}[/dim]")
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                ayahs = [Ayah(**a) for a in data["ayahs"]]
                return SurahText(
                    surah=data["surah"],
                    name_arabic=data["name_arabic"],
                    name_english=data["name_english"],
                    ayah_count=data["ayah_count"],
                    ayahs=ayahs,
                    source=data["source"]
                )
        
        console.print(f"[blue]Fetching Surah {surah} from Quran.com API[/blue]")
        
        # Fetch metadata
        metadata = self._fetch_surah_metadata(surah)
        
        # Fetch verses
        verses = self._fetch_verses(surah)
        
        # Build ayah list
        ayahs = []
        for verse in verses:
            verse_key = verse["verse_key"]  # e.g., "67:1"
            ayah_num = int(verse_key.split(":")[1])
            
            ayahs.append(Ayah(
                surah=surah,
                ayah=ayah_num,
                text_uthmani=verse["text_uthmani"],
                text_simple=verse.get("text_imlaei")
            ))
        
        # Sort by ayah number
        ayahs.sort(key=lambda a: a.ayah)
        
        surah_text = SurahText(
            surah=surah,
            name_arabic=metadata["name_arabic"],
            name_english=metadata["name_simple"],
            ayah_count=metadata["verses_count"],
            ayahs=ayahs,
            source="qurancom"
        )
        
        # Verify integrity
        if not surah_text.verify_integrity():
            raise ValueError(
                f"Integrity check failed for Surah {surah}: "
                f"expected {surah_text.ayah_count} ayahs, got {len(ayahs)}"
            )
        
        # Cache result
        cache_data = {
            "surah": surah_text.surah,
            "name_arabic": surah_text.name_arabic,
            "name_english": surah_text.name_english,
            "ayah_count": surah_text.ayah_count,
            "ayahs": [asdict(a) for a in ayahs],
            "source": surah_text.source
        }
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache_data, f, ensure_ascii=False, indent=2)
        
        console.print(
            f"[green]✓[/green] Loaded {len(ayahs)} ayahs for "
            f"Surah {surah} ({surah_text.name_english})"
        )
        
        return surah_text
    
    def close(self):
        self.client.close()


class TanzilCorpus:
    """
    Load text from Tanzil.net offline corpus.
    
    Download from: https://tanzil.net/download/
    Format: quran-uthmani.txt (simple text format)
    """
    
    def __init__(self, corpus_dir: Path):
        self.corpus_dir = corpus_dir
        self.corpus_file = corpus_dir / "quran-uthmani.txt"
        self._loaded_text: Optional[Dict[str, str]] = None
    
    def _load_corpus(self) -> Dict[str, str]:
        """Load and parse the Tanzil corpus file."""
        if self._loaded_text is not None:
            return self._loaded_text
        
        if not self.corpus_file.exists():
            raise FileNotFoundError(
                f"Tanzil corpus not found at {self.corpus_file}. "
                "Download from https://tanzil.net/download/"
            )
        
        self._loaded_text = {}
        
        with open(self.corpus_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                
                # Format: surah|ayah|text
                parts = line.split("|", 2)
                if len(parts) == 3:
                    surah, ayah, text = parts
                    key = f"{surah}:{ayah}"
                    self._loaded_text[key] = text
        
        return self._loaded_text
    
    def load_surah(self, surah: int) -> SurahText:
        """Load surah from Tanzil corpus."""
        corpus = self._load_corpus()
        
        # Get metadata
        if surah in SURAH_METADATA:
            name_arabic, name_english, expected_count = SURAH_METADATA[surah]
        else:
            name_arabic = f"سورة {surah}"
            name_english = f"Surah {surah}"
            expected_count = None
        
        # Extract ayahs for this surah
        ayahs = []
        ayah_num = 1
        
        while True:
            key = f"{surah}:{ayah_num}"
            if key not in corpus:
                break
            
            ayahs.append(Ayah(
                surah=surah,
                ayah=ayah_num,
                text_uthmani=corpus[key]
            ))
            ayah_num += 1
        
        if not ayahs:
            raise ValueError(f"No ayahs found for Surah {surah} in Tanzil corpus")
        
        return SurahText(
            surah=surah,
            name_arabic=name_arabic,
            name_english=name_english,
            ayah_count=len(ayahs),
            ayahs=ayahs,
            source="tanzil"
        )


def load_surah_text(
    surah: int,
    corpus_dir: Path,
    source: str = "qurancom",
    force_refresh: bool = False
) -> SurahText:
    """
    Load canonical Uthmani text for a surah.
    
    This is the ALIGNMENT TARGET. The text returned here is authoritative
    and will never be modified by any model.
    
    Args:
        surah: Surah number (1-114)
        corpus_dir: Directory for cached corpus data
        source: "qurancom" or "tanzil"
        force_refresh: If True, bypass cache
        
    Returns:
        SurahText with canonical Uthmani text for all ayahs
    """
    if not 1 <= surah <= 114:
        raise ValueError(f"Invalid surah number: {surah}. Must be 1-114.")
    
    console.print(f"\n[bold]═══ LOAD TEXT: Surah {surah} ═══[/bold]\n")
    
    if source == "qurancom":
        corpus = QuranComCorpus(corpus_dir)
        try:
            return corpus.load_surah(surah, force_refresh)
        finally:
            corpus.close()
    elif source == "tanzil":
        corpus = TanzilCorpus(corpus_dir)
        return corpus.load_surah(surah)
    else:
        raise ValueError(f"Unknown corpus source: {source}")


def compute_text_hash(surah_text: SurahText) -> str:
    """
    Compute a hash of the canonical text for integrity verification.
    
    Use this to assert that text has not been modified at any pipeline stage.
    """
    content = "".join(a.text_uthmani for a in surah_text.ayahs)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def assert_text_unchanged(surah_text: SurahText, expected_hash: str) -> None:
    """
    Assert that canonical text matches expected hash.
    
    Call this before emitting output to ensure no model has modified the text.
    """
    actual_hash = compute_text_hash(surah_text)
    if actual_hash != expected_hash:
        raise AssertionError(
            f"CRITICAL: Canonical text has been modified! "
            f"Expected hash {expected_hash}, got {actual_hash}. "
            "This should never happen — text must be preserved verbatim."
        )


if __name__ == "__main__":
    # Quick test
    import os
    from dotenv import load_dotenv
    load_dotenv()
    
    corpus_dir = Path(os.getenv("CORPUS_DIR", "./data/corpus"))
    
    # Test with Al-Mulk
    surah_text = load_surah_text(67, corpus_dir, source="qurancom")
    
    console.print(f"\n[bold]Surah {surah_text.surah}: {surah_text.name_english}[/bold]")
    console.print(f"Ayah count: {surah_text.ayah_count}")
    console.print(f"Text hash: {compute_text_hash(surah_text)}")
    
    # Show first ayah
    console.print(f"\nFirst ayah: {surah_text.ayahs[0].text_uthmani}")
