# Manual Video Ingestion Tool

This directory contains utility scripts and folders to process manual video files (such as music videos or songs) and extract their visual, logo, and audio fingerprints for inclusion in the Content Recognition Platform's reference library.

---

## 📁 Folder Structure

- `manual_ingestion/ingest_video.py` - The ingestion command-line utility.
- `manual_ingestion/videos/` - Directory to place your input video files (e.g. `.mp4`, `.mkv`).
- `manual_ingestion/README.md` - This instruction manual.

---

## ⚙️ Prerequisites

1. **Install Python dependencies** in your virtual environment:
   ```powershell
   .venv\Scripts\pip install opencv-python
   ```
   *Note: `httpx`, `numpy`, and `sqlalchemy` are already pre-installed as part of the core repository dependencies.*

2. **Verify FFmpeg**:
   Ensure `ffmpeg` is available on your system path (pre-verified and available on this system).

---

## 🚀 Usage

Place your video files in the `manual_ingestion/videos/` directory, then execute the tool.

### Option A: Ingest via Server API (Server must be running)
If the backend server is running (e.g. `crp-server` running on `http://127.0.0.1:8000`), the tool will POST directly to the server's reference endpoint:

```powershell
.venv\Scripts\python manual_ingestion/ingest_video.py --video manual_ingestion/videos/your_song.mp4 --title "My Favorite Song" --category music
```

### Option B: Ingest Directly to DB & Qdrant (Offline Ingestion)
If the server is offline or you want to populate the database directly, pass the `--direct` flag. The script will write directly to the local SQLite database and Qdrant collections:

```powershell
.venv\Scripts\python manual_ingestion/ingest_video.py --video manual_ingestion/videos/your_song.mp4 --title "My Favorite Song" --category music --direct
```

---

## 🛠️ Advanced Options

- **`--start-time` / `-s`**: The timestamp in seconds at which the 10-second audio track and target visual/logo keyframes are extracted (default: `10.0`).
- **`--category` / `-c`**: The metadata category of the content. Choices: `music`, `movie`, `series`, `advertisement`, `channel` (default: `music`).
- **`--channel` / `-n`**: Associated channel/source name (default: `Local Library`).
- **`--ocr-keywords` / `-o`**: Searchable OCR keywords (defaults to the lowercase title).
- **`--api-url`**: URL of the platform API (default: `http://127.0.0.1:8000/api/v1/library`).
