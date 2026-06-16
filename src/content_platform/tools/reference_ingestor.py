import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
import numpy as np
import cv2
import librosa

logger = logging.getLogger(__name__)

from content_platform.server.db import (
    Base,
    PlatformBase,
    engine,
    platform_engine,
    SessionLocal,
    PlatformSessionLocal,
)
from content_platform.server.models import (
    Content,
    ContentSegment,
    PlatformReference,
)
from content_platform.fingerprint.unified import UnifiedFingerprinter
from content_platform.server.faiss_index import FAISSIndex
from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor

SEGMENT_SECONDS = 10
FAISS_VISUAL_DIM = 960
FAISS_AUDIO_DIM = 130

FAISS_INDEX_DIR = Path("runtime/faiss")
FAISS_INDEX_DIR.mkdir(parents=True, exist_ok=True)

VISUAL_INDEX_PATH = FAISS_INDEX_DIR / "visual.index"
VISUAL_META_PATH = FAISS_INDEX_DIR / "visual_meta.json"
AUDIO_INDEX_PATH = FAISS_INDEX_DIR / "audio.index"
AUDIO_META_PATH = FAISS_INDEX_DIR / "audio_meta.json"

PLATFORM_VISUAL_INDEX_PATH = FAISS_INDEX_DIR / "platform_visual.index"
PLATFORM_VISUAL_META_PATH = FAISS_INDEX_DIR / "platform_visual_meta.json"

# A mapping dictionary for default platforms corresponding to content directories/files
CONTENT_PLATFORM_MAPPING = {
    "stranger_things": "netflix",
    "mirzapur": "prime_video",
    "dilwale": "netflix",
    "pathaan": "prime_video",
    "inception": "netflix",
    "bangles": "youtube",
    "shararat": "youtube"
}

def get_platform_info(platform_id: str):
    info = {
        "netflix": ("Netflix", "OTT"),
        "youtube": ("YouTube", "OTT"),
        "prime_video": ("Prime Video", "OTT"),
        "sonyliv": ("SonyLIV", "OTT"),
        "jiohotstar": ("JioHotstar", "OTT"),
        "zee5": ("Zee5", "OTT"),
        "sony_max": ("Sony Max", "TV"),
        "star_gold": ("Star Gold", "TV"),
        "zee_cinema": ("Zee Cinema", "TV")
    }
    return info.get(platform_id.lower(), (platform_id.replace("_", " ").title(), "OTT"))

def ingest_platforms(platforms_dir: Path, platform_idx: FAISSIndex):
    print("\n--- INGESTING PLATFORMS ---")
    if not platforms_dir.exists():
        print("No platforms directory found.")
        return

    db_platform = PlatformSessionLocal()
    try:
        platform_folders = [d for d in platforms_dir.iterdir() if d.is_dir()]
        for folder in platform_folders:
            platform_id = folder.name
            plat_name, plat_type = get_platform_info(platform_id)
            print(f"Platform: {plat_name} ({plat_type})")
            
            # Find files (both images and videos)
            files = []
            for ext in ["*.png", "*.jpg", "*.jpeg", "*.webp", "*.mp4"]:
                files.extend(list(folder.glob(ext)))
                
            for file_path in sorted(files):
                print(f"  Ingesting layout file: {file_path.name}")
                if file_path.suffix.lower() == ".mp4":
                    # For video, extract keyframe templates at 10-second intervals
                    cap = cv2.VideoCapture(str(file_path))
                    if not cap.isOpened():
                        continue
                    fps = cap.get(cv2.CAP_PROP_FPS)
                    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                    if fps <= 0 or frame_count <= 0:
                        cap.release()
                        continue
                    duration = int(frame_count / fps)
                    for sec in range(0, duration, SEGMENT_SECONDS):
                        frame_number = int(sec * fps)
                        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                        ret, frame = cap.read()
                        if not ret:
                            continue
                        visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
                        
                        db_item = PlatformReference(
                            platform_id=platform_id,
                            platform_name=plat_name,
                            platform_type=plat_type,
                            visual_fp=json.dumps(visual_fp),
                            ocr_keywords=""
                        )
                        db_platform.add(db_item)
                        db_platform.flush()
                        
                        meta = {
                            "platform_id": platform_id,
                            "platform_name": plat_name,
                            "platform_type": plat_type
                        }
                        platform_idx.add(np.array(visual_fp, dtype=np.float32).reshape(1, -1), [meta])
                    cap.release()
                else:
                    # Image file
                    frame = cv2.imread(str(file_path))
                    if frame is None:
                        continue
                    visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
                    
                    db_item = PlatformReference(
                        platform_id=platform_id,
                        platform_name=plat_name,
                        platform_type=plat_type,
                        visual_fp=json.dumps(visual_fp),
                        ocr_keywords=""
                    )
                    db_platform.add(db_item)
                    db_platform.flush()
                    
                    meta = {
                        "platform_id": platform_id,
                        "platform_name": plat_name,
                        "platform_type": plat_type
                    }
                    platform_idx.add(np.array(visual_fp, dtype=np.float32).reshape(1, -1), [meta])
        db_platform.commit()
    except Exception as e:
        db_platform.rollback()
        print(f"Failed to ingest platforms: {e}")
        raise e
    finally:
        db_platform.close()

def ingest_video_content(video_path: Path, content_id: str, platform_id: str, title: str, content_type: str, series_name: str = None, season_number: int = None, episode_number: int = None, visual_idx=None, audio_idx=None):
    print(f"\nProcessing Video: {video_path.name}")
    db = SessionLocal()
    try:
        # 1. Create or retrieve Content record
        content = db.query(Content).filter(Content.content_id == content_id).first()
        if not content:
            content = Content(
                content_id=content_id,
                platform_id=platform_id,
                title=title,
                content_type=content_type,
                series_name=series_name,
                season_number=season_number,
                episode_number=episode_number
            )
            db.add(content)
            db.flush()

        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")
            
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if fps <= 0:
            raise RuntimeError(f"Invalid FPS: {fps}")
        duration = int(frame_count / fps)
        
        # Load audio (16kHz mono)
        audio = None
        try:
            audio, sr = librosa.load(str(video_path), sr=16000, mono=True)
            if audio.size == 0:
                raise ValueError("Audio loaded is empty")
        except Exception as e:
            logger.warning(f"librosa load failed ({e}); fallback to ffmpeg.")
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                tmp_path = tmp_wav.name
            ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", tmp_path]
            subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            audio, sr = librosa.load(tmp_path, sr=16000, mono=True)
            os.remove(tmp_path)

        print(f"  Duration: {duration}s, Audio Samples: {len(audio)}")
        
        inserted = 0
        for sec in range(0, duration, SEGMENT_SECONDS):
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

            segment_index = sec // SEGMENT_SECONDS
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
            inserted += 1
            
        db.commit()
        print(f"  Successfully ingested {inserted} segments for content: {content_id}")
    except Exception as e:
        db.rollback()
        print(f"  Failed to ingest video {video_path}: {e}")
        raise e
    finally:
        db.close()
        try:
            cap.release()
        except Exception:
            pass

def main():
    print("Resetting database schemas...")
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    PlatformBase.metadata.drop_all(bind=platform_engine)
    PlatformBase.metadata.create_all(bind=platform_engine)

    # Initialize new indexes
    visual_idx = FAISSIndex(dim=FAISS_VISUAL_DIM, gpu=True)
    audio_idx = FAISSIndex(dim=FAISS_AUDIO_DIM, gpu=True)
    platform_idx = FAISSIndex(dim=FAISS_VISUAL_DIM, gpu=True)

    ref_dir = Path("reference_library")
    if not ref_dir.exists():
        ref_dir = Path("manual_ingestion/reference_library")
        
    if not ref_dir.exists():
        print("ERROR: reference_library folder not found.")
        return

    # 1. Ingest Platforms
    ingest_platforms(ref_dir / "platforms", platform_idx)

    # 2. Ingest Movies
    movies_dir = ref_dir / "movies"
    if movies_dir.exists():
        print("\n--- INGESTING MOVIES ---")
        for movie_folder in movies_dir.iterdir():
            if movie_folder.is_dir():
                movie_id = movie_folder.name
                platform_id = CONTENT_PLATFORM_MAPPING.get(movie_id, "unknown")
                title = movie_id.replace("_", " ").title()
                
                # Process mp4 files in movie directory
                for video_file in movie_folder.glob("*.mp4"):
                    ingest_video_content(
                        video_path=video_file,
                        content_id=movie_id,
                        platform_id=platform_id,
                        title=title,
                        content_type="movie",
                        visual_idx=visual_idx,
                        audio_idx=audio_idx
                    )

    # 3. Ingest Series Episodes
    series_dir = ref_dir / "series"
    if series_dir.exists():
        print("\n--- INGESTING SERIES ---")
        for series_folder in series_dir.iterdir():
            if series_folder.is_dir():
                series_id = series_folder.name
                platform_id = CONTENT_PLATFORM_MAPPING.get(series_id, "unknown")
                series_name = series_id.replace("_", " ").title()
                
                # Season subdirectories
                for season_folder in series_folder.iterdir():
                    if season_folder.is_dir() and season_folder.name.startswith("season_"):
                        try:
                            season_number = int(season_folder.name.replace("season_", ""))
                        except ValueError:
                            continue
                            
                        # Episode files
                        for video_file in season_folder.glob("episode_*.mp4"):
                            try:
                                ep_match = re.search(r'episode_(\d+)', video_file.name)
                                if ep_match:
                                    episode_number = int(ep_match.group(1))
                                else:
                                    continue
                            except ValueError:
                                continue
                                
                            content_id = f"{series_id}_s{season_number:02d}_e{episode_number:02d}"
                            title = f"Episode {episode_number}"
                            ingest_video_content(
                                video_path=video_file,
                                content_id=content_id,
                                platform_id=platform_id,
                                title=title,
                                content_type="series",
                                series_name=series_name,
                                season_number=season_number,
                                episode_number=episode_number,
                                visual_idx=visual_idx,
                                audio_idx=audio_idx
                            )

    # Save all FAISS indexes
    visual_idx.save(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH))
    audio_idx.save(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH))
    platform_idx.save(str(PLATFORM_VISUAL_INDEX_PATH), str(PLATFORM_VISUAL_META_PATH))
    print("\nFAISS indexes successfully saved to runtime/faiss/")
    print("ALL REFERENCE LIBRARY INGESTED SUCCESSFULLY!")

if __name__ == "__main__":
    main()