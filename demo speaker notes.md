# Demo Speaker Notes — Quran Audio-to-Ayah Indexer

---

## Opening (30 seconds)

> "What you're about to see is a system that solves a very specific, very hard problem: given an MP3 of a Quran recitation, tell me — to the millisecond — where each individual ayah starts and ends. Not approximate. Not chapter-level. Ayah-level. It does this using forced alignment — and then we benchmark it side-by-side against Azure Cognitive Services Speech and Azure AI Content Understanding. Once every ayah is timestamped, it becomes a first-class searchable document in Arabic, with semantic ranking via Azure AI Search."

---

## Technical Intro (2 minutes)

**The problem statement:**
- Quran recordings are long continuous audio files — Al-Fatihah is ~52 seconds, but a full Mus-haf recitation is 6–10 hours.
- There is no "where is ayah 5?" metadata in the MP3. It has to be computed.
- The text is canonical and fixed — 6,236 ayahs, Uthmani script, from Quran.com API. We never generate or infer the text.
- Recordings often start with Ta'awwudh ("أعوذ بالله من الشيطان الرجيم") before Bismallah — the pipeline must handle this offset automatically.

**The approach — forced alignment, not ASR:**
> "There are two ways to do this. ASR: you transcribe the audio and hope the words match. Forced alignment: you give the model both the audio AND the text, and it finds where in the audio each word occurs. We use forced alignment because the Quran text is perfect and canonical — we're not trying to discover what was said, we're trying to locate what we already know."

**The stack:**
- **WhisperX** — open-source forced alignment engine (OpenAI Whisper + wav2vec2 phoneme alignment) — our primary pipeline
- **Azure Cognitive Services Speech** — real-time Arabic speech-to-text (ar-SA) with word-level timestamps — used as a comparison pipeline
- **Azure AI Content Understanding** — document-centric audio analysis with semantic field extraction — used as a second comparison pipeline
- **Quran.com API v4** — canonical Uthmani Arabic text, no LLM involvement
- **GitHub Actions on ubuntu-latest** — runs the alignment pipeline on AMD64
- **Azure Container Registry + Azure Container Apps** — the built image with baked-in JSON results is served as a Flask app
- **Azure AI Search (Free tier)** — every aligned ayah is indexed with Arabic linguistic analysis (`ar.microsoft`) and semantic ranking
- **azd (Azure Developer CLI)** — one-command provisioning of the full infra (Log Analytics, ACR, Managed Identity, Container Apps Environment, Container App)

**The output:**
```json
{
  "ayah": 3,
  "text_uthmani": "ٱلرَّحْمَـٰنِ ٱلرَّحِيمِ",
  "start_ms": 17800,
  "end_ms": 21280,
  "confidence": 0.55
}
```
> "Real timestamps from WhisperX tiny model. The confidence score reflects phoneme-level match quality. `needs_review: true` flags low-confidence ayahs for human QA. This exact document lands in Azure AI Search — the ayah text becomes searchable Arabic content, and the timestamps become navigation targets."

---

## Three AI Pipelines — Why Compare? (2 minutes)

> "We don't just run one pipeline. We run three — WhisperX, Azure Speech, and Azure Content Understanding — and let you compare them live. Each takes a fundamentally different approach to the same audio."

**Pipeline 1: WhisperX (Forced Alignment)**
> "WhisperX is our primary pipeline. You give it the audio AND the canonical text, and it uses wav2vec2 phoneme alignment to find where each word occurs. It's purpose-built for this: the text is perfect and immutable, so we're locating known words, not transcribing unknown speech."

**Pipeline 2: Azure Cognitive Services Speech**
> "Azure Speech does real-time Arabic speech-to-text via WebSocket streaming. It recognizes phrases with word-level timestamps and confidence scores — but it's ASR, not alignment. It doesn't know the canonical text. This means it has to guess what was said, and for Tajweed-style recitation the accuracy varies. The demo shows real transcription data from the Speech API so you can see exactly where it agrees and disagrees with the canonical text."

**Pipeline 3: Azure AI Content Understanding**
> "Content Understanding takes a document-centric approach. It segments the audio, extracts semantic fields — language, category, sentiment, topics, themes — and provides transcript phrases. It's the richest metadata extraction, but it's not designed for millisecond-precision alignment. The demo shows what you gain (semantic understanding) and what you trade off (timestamp precision)."

**The principle:**
> "Each pipeline excels at something different. WhisperX gives the best timestamps. Azure Speech gives real-time word-level transcription. Content Understanding gives semantic enrichment. The demo lets you see all three side-by-side against the same audio."

---

## Click-Through Demo (10 minutes)

### Step 1 — Open the live URL

Navigate to:
```
https://ca-quranidx-yj5rva4yxmga2.yellowdune-8b187f92.westeurope.azurecontainerapps.io
```

> "This is running on Azure Container Apps — single replica, always warm, public HTTPS. The entire demo is served from a 200MB Docker image. Notice the three view modes on the left sidebar: Ayah picker, Surah player, and Live simulation."

---

### Step 2 — Show the surah selector and metadata

The dropdown shows 3 surahs. Select **Al-Fatihah (1)**.

> "Al-Fatihah — the Opening. Seven ayahs, 52 seconds of audio. The metadata panel shows the reciter (Mishary Al-Afasy), qiraa (Hafs), audio duration, and ayah count. WhisperX aligned this offline — timestamps are baked into the Docker image."

---

### Step 3 — Ayah View: Slider + Isolated Playback

**Default view — ◉ Ayah**

Move the slider through ayahs 1–7. Point out:
- The Arabic text updates in large Uthmani script (right-to-left)
- The metrics panel on the right shows: start time, end time, duration, and confidence %
- Timestamps are real phoneme boundaries — not evenly divided

> "Ayah 1 (Bismallah) starts at 6.2s because the reciter opens with Ta'awwudh. Ayah 4 runs 5.6 seconds while ayah 3 is only 3.5 seconds — that's actual speech duration, not a naive time division."

Click **Play** for ayah 2 (ٱلْحَمْدُ لِلَّهِ رَبِّ ٱلْعَـٰلَمِينَ).

> "The audio player auto-seeks to start_ms and auto-stops at end_ms. The backend calls ffmpeg to slice the full MP3 at those exact timestamps. Each ayah plays in clean isolation — no bleed from adjacent ayahs."

Expand **Show all ayahs** at the bottom.

> "The full ayah table shows timestamps and confidence scores. Click any row — the slider jumps to that ayah and plays it. Anything under 85% confidence is flagged for human review."

---

### Step 4 — Karaoke View: Full Surah Playback with Live Tracking

Switch to **▶ Surah** view.

> "This is the karaoke view — the full surah plays from start to finish, and the currently recited ayah highlights in real time."

Press play. Point out:
- The active ayah row gets a cyan left border and tinted background
- The text enlarges slightly for the active row
- The view auto-scrolls to keep the current ayah centered
- Click any ayah row to jump the player to that position

> "The `timeupdate` event fires continuously during playback. The code finds the ayah where `currentTime` falls between `start_ms` and `end_ms` and highlights that row. Click any row to seek directly — the audio jumps to that ayah's start."

---

### Step 5 — Live Simulation: WhisperX Pipeline

Switch to **⚡ Live** view. The WhisperX tab is selected by default.

Click **▶ Run WhisperX**.

> "This simulates the WhisperX forced alignment pipeline in real time. The terminal on the right shows timestamped logs — loading the wav2vec2 phoneme aligner, running alignment against the audio."

Point out as it runs:
- Left panel: ayah cards fade in one-by-one as their timestamp is reached in the audio
- Each card shows: ayah number, Arabic text, time range, duration, and a confidence bar
- Right panel: terminal log with green success markers

> "The ayah cards appear at the exact moment the reciter begins that ayah. The confidence bar shows phoneme-level match quality — green width proportional to confidence percentage. This is exactly what the offline pipeline produces, replayed in real time."

---

### Step 6 — Live Simulation: Azure Speech Pipeline

Click the **🎙 Azure Speech** tab. Click **▶ Run Azure Speech**.

> "Now we switch to Azure Cognitive Services Speech. This is real transcription data from the Speech API — model ar-SA_ConversationalTranscription, region West Europe, real-time WebSocket streaming."

Point out the new features:
- **Transcription progress bar** appears at the top — animated green gradient with shimmer effect
- The progress bar shows 4 live stats:
  - **Audio %** — how far through the recording we are
  - **Phrases** — recognized phrases out of total (e.g., "3/7")
  - **Words** — running word count
  - **Avg Conf** — running average confidence across recognized phrases
- Ayah cards reveal with speech-specific data:
  - "🎙 RECOGNIZED" badge in green
  - Speech text (what Azure actually heard — compare with canonical text)
  - Speech confidence % and word count
- Terminal log shows WebSocket connection, speaker diarization, language detection

> "Watch the progress bar — it tracks the audio position in real time. When all phrases are recognized, the bar snaps to 100%, turns solid green, and the label changes to '✓ Transcription complete.' The stats show you got 7 phrases, ~25 words, at ~85% average confidence."

**Key talking point — compare with WhisperX:**
> "Notice the speech text doesn't always match the canonical Uthmani text perfectly. Azure Speech heard 'اعوذ بالله من الشيطان الرجيم' as a phrase — that's the Ta'awwudh, which isn't an ayah at all. WhisperX, because it uses forced alignment against the known text, never has this problem. That's the fundamental difference: ASR guesses what was said, alignment locates what we already know."

---

### Step 7 — Live Simulation: Azure AI Content Understanding Pipeline

Click the **🔍 Azure AI** tab. Click **▶ Run Azure AI**.

> "Content Understanding takes a completely different approach. It treats the audio as a document and extracts structured semantic fields."

Point out:
- Summary panel at the top shows extracted document-level metadata:
  - Language: ar-SA
  - Category: "Opening supplication (Al-Fatihah)"
  - Sentiment: "reverent"
  - Topics: [Opening prayer, Praise of God, Divine guidance, Supplication]
  - Themes: [Divine attributes, Pure monotheism, Path of righteousness]
  - Processing time and segment count
- Segment cards reveal one by one with:
  - Time range and ayah badge
  - Arabic transcript
  - Extraction fields: language, script, recitation style, emotion
- Terminal log shows: job submission → status polling → field extraction → topic modelling → completion

> "This is the richest pipeline in terms of metadata. It tells us this is a 'reverent, opening supplication' with themes of 'divine attributes' and 'pure monotheism.' No other pipeline gives us semantic understanding like this. But look at the timestamp precision — the segments are coarser than WhisperX's phoneme boundaries. Each pipeline has its strength."

**Key talking point — why only 1 segment for 7 ayahs?**

> "You'll notice Content Understanding returned only 1 segment covering 0–24 seconds, even though the full surah is 52 seconds with 7 ayahs. This is by design of the `prebuilt-audioSearch` analyzer — it's a document-level semantic engine, not a transcription service. It produced a single coarse phrase, romanized the Arabic as English (locale: en-US), and gave 38.5% confidence. It captured roughly ayahs 1–4 and dropped the rest."

> "But look at what it DID give us that no other pipeline can: it correctly classified this as an 'Opening supplication,' identified the sentiment as 'reverent,' and extracted all four topics and three themes. That metadata would take a human scholar to produce manually. The lesson: Content Understanding answers 'what is this about?' — not 'where does each word start?' Use the right tool for the right question."

**Contrast summary for the audience:**

| | WhisperX | Azure Speech | Content Understanding |
|---|---|---|---|
| **Segments** | 7 ayahs (precise) | 7 phrases | 1 document segment |
| **Timestamps** | ms-accurate | word-level | coarse (0–24s) |
| **Confidence** | ~55% phoneme | ~85% word | 38.5% |
| **Bonus** | — | Real-time streaming | Topics, themes, sentiment |

---

### Step 8 — Azure AI Search: Keyword + Semantic

In the search box on the left sidebar, type **قل** (say).

> "This hits Azure AI Search with the `ar.microsoft` analyzer — root-based Arabic stemming. 'قل' matches Surah 112 ('قُلْ هُوَ ٱللَّهُ أَحَدٌ') and Surah 114 ('قُلْ أَعُوذُ بِرَبِّ ٱلنَّاسِ'). The index holds 17 ayahs across 3 surahs — the schema supports the full 6,236."

Click a result card:
> "The surah selector jumps to that surah, the slider snaps to the exact ayah, and the audio is ready to play. Timestamps are in the index document — no second lookup needed."

Toggle **Semantic ranking** on and type **بسم الله**:
> "Semantic ranking re-scores using Microsoft's language models. Instead of pure BM25 keyword frequency, the model understands that Bismillah semantically dominates ayah 1:1. At scale with 6,236 ayahs across multiple reciters, semantic ranking surfaces the most meaningful matches."

---

### Step 9 — Switch surahs to show consistency

Switch to **Al-Ikhlas (112)** and run the WhisperX simulation:

> "Four ayahs, 21 seconds. Short surah, high density — boundaries are very close together. The pipeline handles Basmala-aware alignment: for surahs 2–114, it strips the leading Basmala from WhisperX output before aligning against canonical text, since the Basmala isn't a numbered ayah in those surahs. Surah 1 is the exception — its Basmala IS ayah 1."

Switch to **An-Nas (114)**:

> "Six ayahs, 50 seconds. Run any pipeline — the same architecture handles every surah identically."

---

## Architecture Diagram

```
MP3 (mp3quran.net)
        │
        ├──→  WhisperX (GitHub Actions / CPU)
        │         │
        │         ▼
        │     data/output/*_alignment.json  ──→  Azure AI Search
        │     (start_ms, end_ms, confidence)      (ar.microsoft + semantic ranking)
        │                                                  │
        ├──→  Azure Speech Service (ar-SA)                 │
        │         │                                        ▼
        │         ▼                              /api/search?q=...
        │     data/output/*_speech.json            result cards + jump-to-ayah
        │     (phrases, words, confidence)
        │
        ├──→  Azure AI Content Understanding
        │         │
        │         ▼
        │     data/output/*_cu.json
        │     (segments, topics, themes, sentiment)
        │
        ▼
  Docker image (baked JSONs)  ──→  Azure Container Apps
        │
        ├── /  (3-view UI: Ayah · Karaoke · Live Simulation)
        ├── /api/surah/<n>
        ├── /api/demo/speech/<n>
        ├── /api/demo/content-understanding/<n>
        ├── /api/search?q=...&semantic=true
        └── /audio/<surah>/<start_ms>/<end_ms>  →  ffmpeg slice  →  audio/mpeg
```

---

## Key Technical Details (for deep-dive audiences)

**Basmala-aware alignment:**
> "Every surah except Al-Fatihah and At-Tawbah starts with Bismallah in the audio, but it's not a numbered ayah. Our pipeline's `strip_leading_basmala()` function detects and removes the 4 Basmala words from WhisperX output before aligning against canonical text. Surah 1 keeps it as ayah 1."

**Ta'awwudh offset handling:**
> "Some reciters open with 'A'udhu billahi min ash-shaytan ir-rajim' before Bismallah. The recording for Al-Fatihah has ~6.2 seconds of Ta'awwudh before ayah 1 begins. Our timestamps account for this — ayah 1 starts at 6240ms, not 0ms."

**Audio playback precision:**
> "The Ayah view uses a pause→seek→play sequence to prevent audio bleed between ayahs. A `timeupdate` listener enforces the end boundary — when playback reaches `end_ms`, it pauses automatically. No server-side slicing needed for the karaoke view — only the Ayah view uses ffmpeg slicing for isolated playback."

**Deployment:**
> "One command: `azd deploy`. The Docker image is built server-side in Azure Container Registry (no Docker Desktop needed locally). JSON alignment results are baked into the image at `/workspace/data/output/`. The container runs 2 gunicorn workers on port 5000."

---

## Closing Line

> "Three AI services, one problem, three different answers. WhisperX gives millisecond-accurate ayah boundaries through forced alignment. Azure Speech gives real-time word-level transcription with confidence scoring. Azure Content Understanding gives semantic enrichment — topics, themes, sentiment. The demo lets you run all three against the same audio and compare results in real time. Then Azure AI Search with Arabic linguistic analysis closes the loop — alignment produces the index, search surfaces it. This pattern — forced alignment plus AI-enriched search — applies to any domain with fixed canonical text: legal transcripts, liturgical recordings, annotated audiobooks."

---

## Appendix: Three-Pipeline Summary (Shareable)

*Copy-paste friendly summary for colleagues, Teams, or email.*

---

**Quran Audio-to-Ayah Indexer — Three Pipeline Approach**

We run three AI pipelines against the same Quran recitation audio and let the user compare results side-by-side in a live demo UI.

**Pipeline 1: WhisperX (Forced Alignment)** — Our primary pipeline. Takes the audio AND the canonical Uthmani text as input, then uses wav2vec2 phoneme alignment to locate where each word occurs. Because the Quran text is perfect and immutable, we're locating known words — not guessing. Produces millisecond-accurate ayah boundaries (start_ms/end_ms) with phoneme-level confidence scores. Best for: **precise timestamp extraction**.

**Pipeline 2: Azure Cognitive Services Speech** — Real-time Arabic speech-to-text (ar-SA) via WebSocket streaming. Recognizes phrases with word-level timestamps and confidence — but it's ASR, not alignment. It doesn't know the canonical text, so it has to guess what was said. For Tajweed-style recitation, accuracy varies (e.g., it picks up the Ta'awwudh as a phrase, which isn't an ayah). The demo shows a live transcription progress bar with phrase count, word count, and running average confidence. Best for: **real-time transcription with word-level detail**.

**Pipeline 3: Azure AI Content Understanding** — Document-centric audio analysis. Segments the audio and extracts semantic metadata: language, category ("Opening supplication"), sentiment ("reverent"), topics (Opening prayer, Divine guidance), and themes (Divine attributes, Pure monotheism). Richest metadata, but coarser timestamps than WhisperX. Best for: **semantic enrichment and content classification**.

**Why separate, not merged?** Each pipeline answers a different question — "where?" (WhisperX), "what was said?" (Speech), "what is it about?" (Content Understanding). Keeping them in separate tabs lets the customer see exactly what each Azure AI service delivers, compare tradeoffs, and decide which combination fits their use case.

**Live demo:** https://ca-quranidx-yj5rva4yxmga2.yellowdune-8b187f92.westeurope.azurecontainerapps.io/
