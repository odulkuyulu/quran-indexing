"""
Unit and Integration Tests for Quran Audio-to-Ayah Indexer

Tests cover:
- Text corpus loading and integrity
- Alignment word-to-ayah mapping
- Scoring calculations
- JSON output validation
- End-to-end pipeline (with mocked audio)
"""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from dataclasses import asdict

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================================
# Text Corpus Tests
# ============================================================================

class TestTextCorpus:
    """Tests for text_corpus module."""
    
    def test_ayah_creation(self):
        """Test Ayah dataclass creation."""
        from src.text_corpus import Ayah
        
        ayah = Ayah(
            surah=1,
            ayah=1,
            text_uthmani="بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"
        )
        
        assert ayah.surah == 1
        assert ayah.ayah == 1
        assert len(ayah.text_uthmani) > 0
    
    def test_ayah_empty_text_raises(self):
        """Test that empty text raises ValueError."""
        from src.text_corpus import Ayah
        
        with pytest.raises(ValueError):
            Ayah(surah=1, ayah=1, text_uthmani="")
        
        with pytest.raises(ValueError):
            Ayah(surah=1, ayah=1, text_uthmani="   ")
    
    def test_surah_text_integrity(self):
        """Test SurahText integrity verification."""
        from src.text_corpus import SurahText, Ayah
        
        ayahs = [
            Ayah(surah=1, ayah=1, text_uthmani="Test 1"),
            Ayah(surah=1, ayah=2, text_uthmani="Test 2"),
            Ayah(surah=1, ayah=3, text_uthmani="Test 3"),
        ]
        
        surah_text = SurahText(
            surah=1,
            name_arabic="Test",
            name_english="Test",
            ayah_count=3,
            ayahs=ayahs,
            source="test"
        )
        
        assert surah_text.verify_integrity() is True
    
    def test_surah_text_integrity_fails_on_gap(self):
        """Test that missing ayahs fail integrity check."""
        from src.text_corpus import SurahText, Ayah
        
        ayahs = [
            Ayah(surah=1, ayah=1, text_uthmani="Test 1"),
            Ayah(surah=1, ayah=3, text_uthmani="Test 3"),  # Missing ayah 2
        ]
        
        surah_text = SurahText(
            surah=1,
            name_arabic="Test",
            name_english="Test",
            ayah_count=3,  # Says 3 but only 2
            ayahs=ayahs,
            source="test"
        )
        
        assert surah_text.verify_integrity() is False
    
    def test_text_hash_consistency(self):
        """Test that text hash is deterministic."""
        from src.text_corpus import SurahText, Ayah, compute_text_hash
        
        ayahs = [
            Ayah(surah=1, ayah=1, text_uthmani="بِسْمِ اللَّهِ"),
            Ayah(surah=1, ayah=2, text_uthmani="الرَّحْمَٰنِ الرَّحِيمِ"),
        ]
        
        surah_text = SurahText(
            surah=1,
            name_arabic="Test",
            name_english="Test",
            ayah_count=2,
            ayahs=ayahs,
            source="test"
        )
        
        hash1 = compute_text_hash(surah_text)
        hash2 = compute_text_hash(surah_text)
        
        assert hash1 == hash2
        assert len(hash1) == 16  # 16 hex characters


# ============================================================================
# Alignment Tests
# ============================================================================

class TestAlignment:
    """Tests for align module."""
    
    def test_normalize_arabic(self):
        """Test Arabic text normalization for alignment."""
        from src.align import normalize_arabic_for_alignment
        
        # With diacritics
        text_with_tashkeel = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"
        normalized = normalize_arabic_for_alignment(text_with_tashkeel)
        
        # Should remove diacritics
        assert "ِ" not in normalized  # Kasra
        assert "ْ" not in normalized  # Sukun
        
    def test_tokenize_arabic(self):
        """Test Arabic tokenization."""
        from src.align import tokenize_arabic
        
        text = "بسم الله الرحمن الرحيم"
        tokens = tokenize_arabic(text)
        
        assert len(tokens) == 4
        assert tokens[0] == "بسم"
        assert tokens[3] == "الرحيم"
    
    def test_word_timing_creation(self):
        """Test WordTiming dataclass."""
        from src.align import WordTiming
        
        wt = WordTiming(
            word="بسم",
            start_ms=0,
            end_ms=500,
            confidence=0.95
        )
        
        assert wt.word == "بسم"
        assert wt.start_ms < wt.end_ms
        assert 0 <= wt.confidence <= 1
    
    def test_ayah_timing_creation(self):
        """Test AyahTiming dataclass."""
        from src.align import AyahTiming
        
        at = AyahTiming(
            surah=1,
            ayah=1,
            text_uthmani="بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ",
            start_ms=0,
            end_ms=4000,
            alignment_confidence=0.92
        )
        
        assert at.surah == 1
        assert at.ayah == 1
        assert len(at.text_uthmani) > 0
        assert at.start_ms < at.end_ms
    
    def test_refine_boundaries_no_gaps(self):
        """Test that boundary refinement removes gaps."""
        from src.align import AyahTiming, refine_ayah_boundaries
        
        timings = [
            AyahTiming(surah=1, ayah=1, text_uthmani="T1", start_ms=0, end_ms=1000),
            AyahTiming(surah=1, ayah=2, text_uthmani="T2", start_ms=1500, end_ms=2500),  # Gap
            AyahTiming(surah=1, ayah=3, text_uthmani="T3", start_ms=2500, end_ms=3500),
        ]
        
        refined = refine_ayah_boundaries(timings, audio_duration_ms=3500)
        
        # Check no gaps between ayahs
        for i in range(1, len(refined)):
            assert refined[i].start_ms == refined[i-1].end_ms or \
                   refined[i].start_ms <= refined[i-1].end_ms + 100


# ============================================================================
# Scoring Tests
# ============================================================================

class TestScoring:
    """Tests for score module."""
    
    def test_normalize_for_comparison(self):
        """Test text normalization for WER comparison."""
        from src.score import normalize_for_comparison
        
        text = "بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"
        normalized = normalize_for_comparison(text)
        
        # Should remove diacritics
        assert "ِ" not in normalized
        assert len(normalized) > 0
    
    def test_score_ayah_high_confidence(self):
        """Test scoring for high-confidence ayah."""
        from src.score import score_ayah
        from src.align import AyahTiming
        
        ayah_timing = AyahTiming(
            surah=1,
            ayah=1,
            text_uthmani="بسم الله الرحمن الرحيم",
            start_ms=0,
            end_ms=4000,
            alignment_confidence=0.95,
            asr_text="بسم الله الرحمن الرحيم"  # Perfect match
        )
        
        score = score_ayah(ayah_timing, confidence_threshold=0.85)
        
        assert score.confidence > 0.85
        assert score.needs_review is False
    
    def test_score_ayah_low_confidence_flags_review(self):
        """Test that low confidence flags for review."""
        from src.score import score_ayah
        from src.align import AyahTiming
        
        ayah_timing = AyahTiming(
            surah=1,
            ayah=1,
            text_uthmani="بسم الله الرحمن الرحيم",
            start_ms=0,
            end_ms=4000,
            alignment_confidence=0.5,  # Low
            asr_text="completely different text"
        )
        
        score = score_ayah(ayah_timing, confidence_threshold=0.85)
        
        assert score.confidence < 0.85
        assert score.needs_review is True
        assert len(score.review_reasons) > 0


# ============================================================================
# Output Tests
# ============================================================================

class TestEmit:
    """Tests for emit module."""
    
    def test_ayah_record_creation(self):
        """Test AyahRecord dataclass."""
        from src.emit import AyahRecord
        
        record = AyahRecord(
            surah=1,
            ayah=1,
            reciter="test_reciter",
            qiraa="Hafs",
            audio_url="https://example.com/audio.mp3",
            start_ms=0,
            end_ms=4000,
            text_uthmani="بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ",
            confidence=0.95,
            needs_review=False
        )
        
        assert record.start_ms < record.end_ms
        assert len(record.text_uthmani) > 0
    
    def test_validate_json_output_valid(self, tmp_path):
        """Test JSON validation with valid output."""
        from src.emit import validate_json_output
        
        valid_data = {
            "surah": 1,
            "ayah_count": 2,
            "ayahs": [
                {
                    "ayah": 1,
                    "start_ms": 0,
                    "end_ms": 2000,
                    "text_uthmani": "Test 1"
                },
                {
                    "ayah": 2,
                    "start_ms": 2000,
                    "end_ms": 4000,
                    "text_uthmani": "Test 2"
                }
            ]
        }
        
        json_path = tmp_path / "test.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(valid_data, f)
        
        result = validate_json_output(json_path)
        
        assert result["valid"] is True
        assert len(result["errors"]) == 0
    
    def test_validate_json_output_invalid_timing(self, tmp_path):
        """Test JSON validation catches invalid timing."""
        from src.emit import validate_json_output
        
        invalid_data = {
            "surah": 1,
            "ayah_count": 1,
            "ayahs": [
                {
                    "ayah": 1,
                    "start_ms": 5000,  # Start > End
                    "end_ms": 2000,
                    "text_uthmani": "Test"
                }
            ]
        }
        
        json_path = tmp_path / "test.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(invalid_data, f)
        
        result = validate_json_output(json_path)
        
        assert result["valid"] is False
        assert any("start_ms" in e for e in result["errors"])
    
    def test_validate_json_output_empty_text(self, tmp_path):
        """Test JSON validation catches empty text."""
        from src.emit import validate_json_output
        
        invalid_data = {
            "surah": 1,
            "ayah_count": 1,
            "ayahs": [
                {
                    "ayah": 1,
                    "start_ms": 0,
                    "end_ms": 2000,
                    "text_uthmani": ""  # Empty
                }
            ]
        }
        
        json_path = tmp_path / "test.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(invalid_data, f)
        
        result = validate_json_output(json_path)
        
        assert result["valid"] is False
        assert any("empty" in e.lower() for e in result["errors"])


# ============================================================================
# Integration Tests
# ============================================================================

class TestIntegration:
    """Integration tests for complete pipeline."""
    
    @pytest.mark.integration
    def test_corpus_api_connection(self):
        """Test that Quran.com API is accessible."""
        import httpx
        
        response = httpx.get(
            "https://api.quran.com/api/v4/chapters/1",
            timeout=10.0
        )
        
        assert response.status_code == 200
        data = response.json()
        assert "chapter" in data
    
    @pytest.mark.integration
    def test_load_al_fatiha(self, tmp_path):
        """Test loading Al-Fatiha from API."""
        from src.text_corpus import load_surah_text
        
        surah_text = load_surah_text(
            surah=1,
            corpus_dir=tmp_path,
            source="qurancom"
        )
        
        assert surah_text.surah == 1
        assert surah_text.ayah_count == 7
        assert len(surah_text.ayahs) == 7
        assert surah_text.verify_integrity() is True


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def sample_ayahs():
    """Create sample ayahs for testing."""
    from src.text_corpus import Ayah
    
    return [
        Ayah(surah=1, ayah=1, text_uthmani="بِسْمِ اللَّهِ الرَّحْمَٰنِ الرَّحِيمِ"),
        Ayah(surah=1, ayah=2, text_uthmani="الْحَمْدُ لِلَّهِ رَبِّ الْعَالَمِينَ"),
        Ayah(surah=1, ayah=3, text_uthmani="الرَّحْمَٰنِ الرَّحِيمِ"),
    ]


@pytest.fixture
def sample_word_timings():
    """Create sample word timings for testing."""
    from src.align import WordTiming
    
    return [
        WordTiming(word="بسم", start_ms=0, end_ms=500, confidence=0.9),
        WordTiming(word="الله", start_ms=500, end_ms=900, confidence=0.95),
        WordTiming(word="الرحمن", start_ms=900, end_ms=1300, confidence=0.88),
        WordTiming(word="الرحيم", start_ms=1300, end_ms=1800, confidence=0.92),
    ]


# ============================================================================
# Run tests
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
