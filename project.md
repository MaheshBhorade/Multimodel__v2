# Content Recognition Platform (CRP) - Project Guide for AI Agents

Welcome, Agent! This document details the architecture, codebase structure, database schema, matching mechanics, and latest optimizations for the Content Recognition Platform. Use this to quickly orient yourself and continue development.

---

## 🎯 System Architecture & Data Flow

This is a distributed real-time content recognition system designed to identify TV and OTT broadcasts from edge captures.

1. **Edge Client (Raspberry Pi / Simulator)**: Captures video and audio streams. Every 10 seconds, it POSTs a payload containing:
   - Visual fingerprints (`visual_fps`): A list of 10 lists of float vectors (960-dimensional embeddings) representing each of the 10 seconds in the batch.
   - Audio fingerprint (`audio_fp`): A 130-dimensional float vector representing the overall audio characteristics.
   - Snapshot image: A single JPG frame taken at the `best_frame_index`.
2. **FastAPI Server**:
   - Ingestion: Receives the 10-second batch via `/api/v1/captures` and expands it into 10 separate 1-second `Capture` records in the SQLite database (using `captured_at` incremented by 1-second ticks). Only the capture at `best_frame_index` stores the `snapshot_url`.
   - Matching: Executes a background task (`process_matching_background`) to match each of the 10 captures sequentially using FAISS vector indexing (visual and audio).
   - Session State: At the end of matching the batch, a temporal state machine (`rebuild_playback_sessions_for_device`) rebuilds playback sessions based on matched content continuity.

---

## 📂 Codebase Structure

- `src/content_platform/`
  - `shared/`
    - [config.py](file:///d:/Multimodel_Fingerprint/src/content_platform/shared/config.py): Configuration settings (SQLite file path, Qdrant URL, etc.).
    - [models.py](file:///d:/Multimodel_Fingerprint/src/content_platform/shared/models.py): Pydantic payload and response schemas (e.g., `FingerprintPayload`).
  - `server/`
    - [main.py](file:///d:/Multimodel_Fingerprint/src/content_platform/server/main.py): FastAPI server, API endpoints, background matching orchestration.
    - [db.py](file:///d:/Multimodel_Fingerprint/src/content_platform/server/db.py): SQLAlchemy session lifecycle setup (SQLite in WAL mode, NullPool to prevent lock contention).
    - [models.py](file:///d:/Multimodel_Fingerprint/src/content_platform/server/models.py): SQLAlchemy database schema (`Device`, `Capture`, `ContentLibrary`, `RecognitionResultRecord`, `PlaybackSession`).
    - [matching.py](file:///d:/Multimodel_Fingerprint/src/content_platform/server/matching.py): Core matching engine. Implements FAISS indexing search, fallback brute-force matching, similarity formulas (visual, audio, OCR), and segment voting logic.
    - [faiss_index.py](file:///d:/Multimodel_Fingerprint/src/content_platform/server/faiss_index.py): GPU-to-CPU fallback FAISS index manager.
    - [temporal.py](file:///d:/Multimodel_Fingerprint/src/content_platform/server/temporal.py): Temporal state machine calculating continuous playback sessions.
  - `edge/`
    - [main.py](file:///d:/Multimodel_Fingerprint/src/content_platform/edge/main.py): Entry point for edge client.
    - [agent.py](file:///d:/Multimodel_Fingerprint/src/content_platform/edge/agent.py): Infinite loop capturing and submitting payloads.
    - [extractors.py](file:///d:/Multimodel_Fingerprint/src/content_platform/edge/extractors.py): Extractors for simulated mode or real hardware (ffmpeg/OpenCV/librosa).
- `tests/`: `pytest` test suite covering API ingestion, dashboard, matching, and temporal calculations.

---

## ⚡ Database Schema (SQLite)

- **devices**: Active edge devices (`device_id`).
- **captures**: Ingested captures with 1-second resolution. Stores JSON strings of `visual_fp` and `audio_fp`, capture metadata, and `status` (`pending`, `matched`, `failed`).
- **content_library**: Reference library containing seeded segments of known channels and episodes.
- **recognition_results**: Matching results (`confidence`, `visual_score`, `audio_score`). Linked 1-to-1 with `captures`.
- **playback_sessions**: Rebuilt continuous viewing logs with start, end, and duration.

---

## ⚙️ Matching Mechanics & Thresholds

- **Adaptive Scoring**:
  - Full Multimodal: `Score = (Visual * 0.70) + (Audio * 0.30)`.
  - Audio Missing (Pi recording failure / all zeros): `Score = Visual Similarity`.
  - Visual Blank (black screen / green screen): `Score = Audio Similarity`.
- **Thresholds**:
  - Match threshold: `0.75` for standard matching, `0.60` if audio is missing.
- **Aggregations**:
  - FAISS search returns multiple segments belonging to the same content. The aggregation dict must keep the **maximum similarity** for each unique `external_content_id`.
  - The final winner is decided by segment voting across top-10 hits, sorted by maximum individual segment score first (with vote count as tie-breaker).

---

## 🛠️ Performance & Testing Rules

1. **Keep Database Queries Localized**: When writing matching code, do not load the entire `ContentLibrary` from SQLite unless a fallback to brute-force is actually triggered. Doing so under concurrency causes database lock contention and HTTP client timeouts.
2. **Transaction Scopes**: Perform database write operations in batch transactions and commit once.
3. **Execution Commands**:
   - Start Server: `python -m uvicorn content_platform.server.main:app --host 0.0.0.0 --port 8000` (or `crp-server`)
   - Start Edge Simulator: `python -m content_platform.edge.main` (or `crp-edge`)
   - Run Test Suite: `pytest -v`
