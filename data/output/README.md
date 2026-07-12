# Output Data Directory

This directory contains generated per-ayah indices:

- `{surah:03d}_{reciter}.json` - Per-surah index files

Each JSON file contains:
- Surah metadata
- Per-ayah records with:
  - Canonical Uthmani text (verbatim from corpus)
  - start_ms / end_ms timestamps
  - Confidence scores
  - Review flags
