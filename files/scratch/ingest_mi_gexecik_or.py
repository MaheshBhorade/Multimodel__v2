import json
import os
import re
import sys
import tempfile
import subprocess
from pathlib import Path
import numpy as np
import cv2
import librosa

# Add project root to sys.path to allow imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from content_platform.server.db import SessionLocal
from content_platform.server.models import Content, ContentSegment
from content_platform.fingerprint.unified import UnifiedFingerprinter
from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor
from content_platform.server.faiss_index import FAISSIndex

VIDEO_DIR = Path(r"D:\Multimodel_Fingerprint\MI GEXECIK OR")
FAISS_INDEX_DIR = Path("runtime/faiss")
VISUAL_INDEX_PATH = FAISS_INDEX_DIR / "visual.index"
VISUAL_META_PATH = FAISS_INDEX_DIR / "visual_meta.json"
AUDIO_INDEX_PATH = FAISS_INDEX_DIR / "audio.index"
AUDIO_META_PATH = FAISS_INDEX_DIR / "audio_meta.json"

FAISS_VISUAL_DIM = 960
FAISS_AUDIO_DIM = 130
SEGMENT_SECONDS = 10

def load_audio_via_ffmpeg(video_path: Path, duration: float = None) -> tuple[np.ndarray, int]:
    """Helper to extract audio to a temporary WAV and load it with librosa, avoiding slow audioread."""
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        tmp_path = tmp_wav.name
    try:
        ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(video_path)]
        if duration:
            ffmpeg_cmd += ["-t", str(duration)]
        ffmpeg_cmd += ["-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", tmp_path]
        subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        audio, sr = librosa.load(tmp_path, sr=16000, mono=True)
        return audio, sr
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def main():
    if not VIDEO_DIR.exists():
        print(f"Error: Path '{VIDEO_DIR}' does not exist.", flush=True)
        sys.exit(1)

    # Load existing FAISS indexes or create new
    if VISUAL_INDEX_PATH.exists() and AUDIO_INDEX_PATH.exists():
        print("Loading existing FAISS indexes from disk...", flush=True)
        visual_idx = FAISSIndex.load(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH), dim=FAISS_VISUAL_DIM, gpu=True)
        audio_idx = FAISSIndex.load(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH), dim=FAISS_AUDIO_DIM, gpu=True)
    else:
        print("Initializing new FAISS indexes...", flush=True)
        visual_idx = FAISSIndex(dim=FAISS_VISUAL_DIM, gpu=True)
        audio_idx = FAISSIndex(dim=FAISS_AUDIO_DIM, gpu=True)

    video_files = sorted(
        [f for f in VIDEO_DIR.iterdir() if f.is_file() and f.suffix.lower() == ".mp4"],
        key=lambda x: int(re.search(r"Episode\s*(\d+)", x.name).group(1)) if re.search(r"Episode\s*(\d+)", x.name) else 0
    )

    if not video_files:
        print("No MP4 files found.", flush=True)
        sys.exit(1)

    print(f"Found {len(video_files)} video files.", flush=True)

    db = SessionLocal()
    try:
        # 1. Ingest Intro once (using the first 7 seconds of the first video)
        intro_content_id = "series-mi_gexecik_or-intro"
        intro_title = "MI GEXECIK OR - Intro"
        existing_intro = db.query(Content).filter(Content.content_id == intro_content_id).first()
        if not existing_intro:
            print("Ingesting global intro...", flush=True)
            db_intro = Content(
                content_id=intro_content_id,
                platform_id="global",
                title=intro_title,
                content_type="series",
                series_name="MI GEXECIK OR",
                season_number=None,
                episode_number=None
            )
            db.add(db_intro)
            db.flush()

            # Extract segment for intro
            first_video = video_files[0]
            cap = cv2.VideoCapture(str(first_video))
            
            # Read frame at 0s
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
            if ret:
                visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
                
                # Load first 10 seconds of audio for intro signature using FFmpeg
                audio, sr = load_audio_via_ffmpeg(first_video, duration=10.0)
                if len(audio) < 100:
                    audio = np.zeros(sr * 10, dtype=np.float32)
                audio_fp = UnifiedFingerprinter.audio_fingerprint(audio, sr)

                db_item = ContentSegment(
                    content_id=intro_content_id,
                    segment_index=0,
                    segment_offset=0,
                    visual_fp=json.dumps(visual_fp),
                    audio_fp=json.dumps(audio_fp)
                )
                db.add(db_item)
                db.flush()

                meta = {
                    "content_id": intro_content_id,
                    "segment_index": 0,
                    "segment_offset": 0
                }
                visual_idx.add(np.array(visual_fp, dtype=np.float32).reshape(1, -1), [meta])
                audio_idx.add(np.array(audio_fp, dtype=np.float32).reshape(1, -1), [meta])
                print("Global intro successfully ingested.", flush=True)
            cap.release()

        # 2. Ingest all episodes starting from 7 seconds offset
        for idx, video_file in enumerate(video_files, 1):
            ep_match = re.search(r"Episode\s*(\d+)", video_file.name)
            if not ep_match:
                print(f"Skipping file without Episode number: {video_file.name}", flush=True)
                continue
            episode_num = int(ep_match.group(1))
            content_id = f"series-mi_gexecik_or-s01-e{episode_num:02d}"
            display_title = f"MI GEXECIK OR S01E{episode_num:02d}"

            # Check if content already exists
            existing_content = db.query(Content).filter(Content.content_id == content_id).first()
            if not existing_content:
                db_content = Content(
                    content_id=content_id,
                    platform_id="unknown",
                    title=display_title,
                    content_type="series",
                    series_name="MI GEXECIK OR",
                    season_number=1,
                    episode_number=episode_num
                )
                db.add(db_content)
                db.flush()

            cap = cv2.VideoCapture(str(video_file))
            if not cap.isOpened():
                print(f"Cannot open video: {video_file}", flush=True)
                continue

            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = int(frame_count / fps)

            print(f"[{idx}/{len(video_files)}] Ingesting {video_file.name} (Duration: {duration}s, skipping first 7s)...", flush=True)
            
            # Load audio for the whole video using FFmpeg
            audio, sr = load_audio_via_ffmpeg(video_file)

            for sec in range(7, duration, SEGMENT_SECONDS):
                segment_index = (sec - 7) // SEGMENT_SECONDS

                # Check if segment already exists in DB
                existing_seg = db.query(ContentSegment).filter(
                    ContentSegment.content_id == content_id,
                    ContentSegment.segment_index == segment_index
                ).first()
                if existing_seg:
                    continue

                frame_number = int(sec * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                ret, frame = cap.read()
                if not ret:
                    continue

                visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
                if len(visual_fp) != FAISS_VISUAL_DIM:
                    continue

                start_audio = sec * sr
                end_audio = min(len(audio), (sec + SEGMENT_SECONDS) * sr)
                audio_chunk = audio[start_audio:end_audio]
                if len(audio_chunk) < 100:
                    audio_chunk = np.zeros(sr * SEGMENT_SECONDS, dtype=np.float32)

                audio_fp = UnifiedFingerprinter.audio_fingerprint(audio_chunk, sr)
                if len(audio_fp) != FAISS_AUDIO_DIM:
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

            cap.release()
            db.commit()

        # Save updated FAISS indexes
        print("Saving updated FAISS indexes to disk...", flush=True)
        visual_idx.save(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH))
        audio_idx.save(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH))
        print("Ingestion complete!", flush=True)

    except Exception as e:
        db.rollback()
        print(f"Error occurred: {e}", flush=True)
        raise
    finally:
        db.close()

if __name__ == "__main__":
    main()
