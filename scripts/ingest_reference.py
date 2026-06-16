import argparse
import json
import logging
import os
import re
import sys
import tempfile
import shutil
import subprocess
from pathlib import Path
import numpy as np
import cv2
import librosa
from tqdm import tqdm

# Add project root to sys.path to allow imports of content_platform
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from content_platform.server.db import SessionLocal, engine, platform_engine, Base, PlatformBase
from content_platform.server.models import Content, ContentSegment
from content_platform.fingerprint.unified import UnifiedFingerprinter
from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor
from content_platform.server.faiss_index import FAISSIndex

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ingest_reference")

SEGMENT_SECONDS = 10
FAISS_VISUAL_DIM = 960
FAISS_AUDIO_DIM = 130

FAISS_INDEX_DIR = Path("runtime/faiss")
FAISS_INDEX_DIR.mkdir(parents=True, exist_ok=True)
VISUAL_INDEX_PATH = FAISS_INDEX_DIR / "visual.index"
VISUAL_META_PATH = FAISS_INDEX_DIR / "visual_meta.json"
AUDIO_INDEX_PATH = FAISS_INDEX_DIR / "audio.index"
AUDIO_META_PATH = FAISS_INDEX_DIR / "audio_meta.json"


def parse_metadata_from_filename(filename: str):
    """
    Parses metadata from filename.
    Returns: content_type ('movie' or 'series'), title/series_name, season_number, episode_number
    """
    base = os.path.splitext(filename)[0]
    
    # Check for S00E00 pattern
    se_match = re.search(r'[sS](\d+)\s*[eE](\d+)', base)
    if se_match:
        season = int(se_match.group(1))
        episode = int(se_match.group(2))
        series = base[:se_match.start()].strip(" -_")
        series = re.sub(r'[_]+', ' ', series).strip()
        return "series", series, season, episode

    # Check for Episode X pattern
    ep_match = re.search(r'(?:episode|ep|ep\.)\s*(\d+)', base, re.IGNORECASE)
    if ep_match:
        episode = int(ep_match.group(1))
        series = base[:ep_match.start()].strip(" -_")
        series = re.sub(r'[_]+', ' ', series).strip()
        
        s_match = re.search(r'(?:season|s)\s*(\d+)', base, re.IGNORECASE)
        season = int(s_match.group(1)) if s_match else 1
        return "series", series, season, episode

    # Default to movie
    title = re.sub(r'[_]+', ' ', base).strip()
    return "movie", title, None, None


def ingest_single_video(video_path: Path, args, visual_idx: FAISSIndex, audio_idx: FAISSIndex):
    # 1. Parse or override metadata
    parsed_type, parsed_title, parsed_season, parsed_episode = parse_metadata_from_filename(video_path.name)
    
    content_type = args.type or parsed_type
    platform_id = args.platform or "unknown"
    title = args.title or parsed_title
    
    series_name = None
    season_number = None
    episode_number = None
    
    if content_type == "series":
        series_name = title
        season_number = args.season if args.season is not None else parsed_season
        episode_number = args.episode if args.episode is not None else parsed_episode
        content_id = f"series-{series_name.lower().replace(' ', '_')}-s{season_number:02d}-e{episode_number:02d}"
        display_title = f"{series_name} S{season_number:02d}E{episode_number:02d}"
    else:
        content_id = f"movie-{title.lower().replace(' ', '_')}"
        display_title = title

    db = SessionLocal()
    try:
        # Check if content already exists
        existing_content = db.query(Content).filter(Content.content_id == content_id).first()
        if existing_content:
            pass
        else:
            db_content = Content(
                content_id=content_id,
                platform_id=platform_id,
                title=display_title,
                content_type=content_type,
                series_name=series_name,
                season_number=season_number,
                episode_number=episode_number,
                language=args.language,
                genre=args.genre
            )
            db.add(db_content)
            db.flush()

        # 2. Extract video & audio features
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")
            
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0 or frame_count <= 0:
            raise RuntimeError(f"Invalid video parameters (FPS={fps}, frame_count={frame_count})")
        duration = int(frame_count / fps)
        
        # Load audio (16kHz mono)
        audio = None
        try:
            audio, sr = librosa.load(str(video_path), sr=16000, mono=True)
        except Exception as e:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                tmp_path = tmp_wav.name
            ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", tmp_path]
            subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            audio, sr = librosa.load(tmp_path, sr=16000, mono=True)
            os.remove(tmp_path)
            
        num_segments = duration // SEGMENT_SECONDS
        
        # Real-time progress bar for current video segments
        with tqdm(total=num_segments, desc=f"Ingesting {video_path.name[:30]}", leave=False) as pbar:
            for sec in range(0, duration, SEGMENT_SECONDS):
                segment_index = sec // SEGMENT_SECONDS
                
                # Check if segment already exists in DB
                existing_seg = db.query(ContentSegment).filter(
                    ContentSegment.content_id == content_id,
                    ContentSegment.segment_index == segment_index
                ).first()
                if existing_seg:
                    pbar.update(1)
                    continue

                frame_number = int(sec * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                ret, frame = cap.read()
                if not ret:
                    pbar.update(1)
                    continue

                visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
                if len(visual_fp) != FAISS_VISUAL_DIM:
                    pbar.update(1)
                    continue
                    
                start_audio = sec * sr
                end_audio = min(len(audio), (sec + SEGMENT_SECONDS) * sr)
                audio_chunk = audio[start_audio:end_audio]
                if len(audio_chunk) < 100:
                    audio_chunk = np.zeros(sr * SEGMENT_SECONDS, dtype=np.float32)
                    
                audio_fp = UnifiedFingerprinter.audio_fingerprint(audio_chunk, sr)
                if len(audio_fp) != FAISS_AUDIO_DIM:
                    pbar.update(1)
                    continue

                db_item = ContentSegment(
                    content_id=content_id,
                    segment_index=segment_index,
                    segment_offset=sec,
                    visual_fp=json.dumps(visual_fp),
                    audio_fp=json.dumps(audio_fp)
                )
                db.add(db_item)
                db.flush()

                meta = {
                    "content_id": content_id,
                    "segment_index": segment_index,
                    "segment_offset": sec
                }
                visual_idx.add(np.array(visual_fp, dtype=np.float32).reshape(1, -1), [meta])
                audio_idx.add(np.array(audio_fp, dtype=np.float32).reshape(1, -1), [meta])
                pbar.update(1)
                
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"  Failed to ingest video {video_path.name}: {e}")
    finally:
        db.close()
        try:
            cap.release()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="CRP Reference Library Bulk Ingestor Tool")
    parser.add_argument("--path", required=True, help="Path to video file or directory of video files")
    parser.add_argument("--type", choices=["movie", "series"], help="Explicitly set content type (movie or series)")
    parser.add_argument("--platform", help="Platform ID (e.g. netflix, youtube, prime_video)")
    parser.add_argument("--title", help="Explicit title / series name override")
    parser.add_argument("--season", type=int, help="Explicit season number override (for series)")
    parser.add_argument("--episode", type=int, help="Explicit episode number override (for series)")
    parser.add_argument("--language", help="Content language")
    parser.add_argument("--genre", help="Content genre")
    parser.add_argument("--reset", action="store_true", help="Delete and recreate database and FAISS indexes first")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild FAISS indexes completely from the database instead of appending")
    args = parser.parse_args()

    input_path = Path(args.path)
    if not input_path.exists():
        print(f"Error: Path '{input_path}' does not exist.")
        sys.exit(1)

    if args.reset:
        print("Resetting database schemas (dropping and recreating tables)...")
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        PlatformBase.metadata.drop_all(bind=platform_engine)
        PlatformBase.metadata.create_all(bind=platform_engine)
        
        # Delete existing FAISS indexes
        if FAISS_INDEX_DIR.exists():
            shutil.rmtree(FAISS_INDEX_DIR)
        FAISS_INDEX_DIR.mkdir(parents=True, exist_ok=True)

    # Load or rebuild FAISS indexes
    db = SessionLocal()
    db_count = db.query(ContentSegment).count()
    db.close()

    if args.reset or args.rebuild or not (VISUAL_INDEX_PATH.exists() and AUDIO_INDEX_PATH.exists()):
        print("Initializing new FAISS indexes for full rebuild...")
        visual_idx = FAISSIndex(dim=FAISS_VISUAL_DIM, gpu=True)
        audio_idx = FAISSIndex(dim=FAISS_AUDIO_DIM, gpu=True)
        
        # Populate from existing database elements
        db = SessionLocal()
        existing_items = db.query(ContentSegment).all()
        if existing_items:
            print(f"Loading {len(existing_items)} existing segments from DB...")
            for item in existing_items:
                try:
                    vis_fp = json.loads(item.visual_fp)
                    aud_fp = json.loads(item.audio_fp)
                    meta = {
                        "content_id": item.content_id,
                        "segment_index": item.segment_index,
                        "segment_offset": item.segment_offset
                    }
                    if len(vis_fp) == FAISS_VISUAL_DIM:
                        visual_idx.add(np.array(vis_fp, dtype=np.float32).reshape(1, -1), [meta])
                    if len(aud_fp) == FAISS_AUDIO_DIM:
                        audio_idx.add(np.array(aud_fp, dtype=np.float32).reshape(1, -1), [meta])
                except Exception:
                    pass
        db.close()
    else:
        print("Loading existing FAISS indexes from disk...")
        visual_idx = FAISSIndex.load(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH), dim=FAISS_VISUAL_DIM, gpu=True)
        audio_idx = FAISSIndex.load(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH), dim=FAISS_AUDIO_DIM, gpu=True)

    # 2. Collect files to process
    video_files = []
    video_extensions = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm")
    if input_path.is_file():
        if input_path.suffix.lower() in video_extensions:
            video_files.append(input_path)
    else:
        for file in sorted(input_path.rglob("*")):
            if file.is_file() and file.suffix.lower() in video_extensions:
                video_files.append(file)

    if not video_files:
        print(f"No valid video files found at path '{input_path}'.")
        sys.exit(0)

    print(f"Found {len(video_files)} video file(s) to process.")

    # 3. Process video files
    # Outer progress bar
    for idx, video_file in enumerate(tqdm(video_files, desc="Total Progress")):
        ingest_single_video(video_file, args, visual_idx, audio_idx)

    # 4. Save updated indexes
    print("\nSaving updated FAISS indexes to disk...")
    visual_idx.save(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH))
    audio_idx.save(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH))
    print("FAISS indexes successfully saved to runtime/faiss/")
    print("INGESTION WORKFLOW COMPLETED SUCCESSFULLY!")


if __name__ == "__main__":
    main()
