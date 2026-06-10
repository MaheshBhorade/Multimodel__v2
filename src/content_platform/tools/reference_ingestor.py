import json
import uuid
from pathlib import Path

import cv2
import librosa
import logging
import subprocess
import tempfile
import os
logger = logging.getLogger(__name__)

from content_platform.server.db import (
    Base,
    engine,
    SessionLocal,
)
from content_platform.server.models import (
    ContentLibrary,
)
from content_platform.fingerprint.unified import (
    UnifiedFingerprinter,
)
import numpy as np
from content_platform.server.faiss_index import FAISSIndex
from pathlib import Path
from content_platform.fingerprint.embeddings import (
    DeepEmbeddingsExtractor,
)

SEGMENT_SECONDS = 10

# FAISS index configuration
FAISS_VISUAL_DIM = 960
FAISS_AUDIO_DIM = 130
FAISS_INDEX_DIR = Path("runtime/faiss")
FAISS_INDEX_DIR.mkdir(parents=True, exist_ok=True)
VISUAL_INDEX_PATH = FAISS_INDEX_DIR / "visual.index"
VISUAL_META_PATH = FAISS_INDEX_DIR / "visual_meta.json"
AUDIO_INDEX_PATH = FAISS_INDEX_DIR / "audio.index"
AUDIO_META_PATH = FAISS_INDEX_DIR / "audio_meta.json"

# Load or create indexes
# Load or create indexes with dimension validation
if VISUAL_INDEX_PATH.exists() and VISUAL_META_PATH.exists():
    visual_index = FAISSIndex.load(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH), dim=FAISS_VISUAL_DIM, gpu=True)
    # Verify dimension compatibility
    if getattr(visual_index.index, 'd', None) != FAISS_VISUAL_DIM:
        logger.warning("Existing visual FAISS index dimension mismatch; recreating index.")
        visual_index = FAISSIndex(dim=FAISS_VISUAL_DIM, gpu=True)
else:
    visual_index = FAISSIndex(dim=FAISS_VISUAL_DIM, gpu=True)

if AUDIO_INDEX_PATH.exists() and AUDIO_META_PATH.exists():
    audio_index = FAISSIndex.load(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH), dim=FAISS_AUDIO_DIM, gpu=True)
    if getattr(audio_index.index, 'd', None) != FAISS_AUDIO_DIM:
        logger.warning("Existing audio FAISS index dimension mismatch; recreating index.")
        audio_index = FAISSIndex(dim=FAISS_AUDIO_DIM, gpu=True)
else:
    audio_index = FAISSIndex(dim=FAISS_AUDIO_DIM, gpu=True)



def audio_to_string(fp: list[float]) -> str:
    return "ae-hash-" + "-".join(
        f"{x:.6f}" for x in fp
    )


def ingest_video(
    video_path: str,
    title: str,
    category: str = "song",
):

    db = SessionLocal()

    try:

        content_id = str(uuid.uuid4())

        print("\n" + "=" * 80)
        print(f"PROCESSING : {title}")
        print(f"FILE       : {video_path}")
        print("=" * 80)

        cap = cv2.VideoCapture(video_path)

        print("OPENED =", cap.isOpened())

        if not cap.isOpened():
            raise RuntimeError(
                f"Cannot open video: {video_path}"
            )

        fps = cap.get(
            cv2.CAP_PROP_FPS
        )

        frame_count = int(
            cap.get(
                cv2.CAP_PROP_FRAME_COUNT
            )
        )

        print("FPS         =", fps)
        print("FRAME_COUNT =", frame_count)

        if fps <= 0:
            raise RuntimeError(
                f"Invalid FPS: {fps}"
            )

        duration = int(
            frame_count / fps
        )

        print(
            f"DURATION = {duration}s"
        )

        print("Loading audio...")

        audio = None
        try:
            audio, sr = librosa.load(
                video_path,
                sr=16000,
                mono=True,
            )
            if audio.size == 0:
                raise ValueError("Loaded audio is empty")
        except Exception as e:
            logger.warning(f"librosa failed to load audio ({e}); falling back to ffmpeg extraction.")
            # Create temporary wav file
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                tmp_path = tmp_wav.name
            ffmpeg_cmd = [
                "ffmpeg",
                "-y",
                "-i",
                video_path,
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                tmp_path,
            ]
            subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            audio, sr = librosa.load(tmp_path, sr=16000, mono=True)
            os.remove(tmp_path)
            if audio.size == 0:
                raise ValueError("Extracted audio is empty after ffmpeg fallback")

        print(
            f"Audio loaded. Samples={len(audio)} SR={sr}"
        )

        inserted = 0

        for sec in range(
            0,
            duration,
            SEGMENT_SECONDS
        ):

            frame_number = int(
                sec * fps
            )

            cap.set(
                cv2.CAP_PROP_POS_FRAMES,
                frame_number
            )

            ret, frame = cap.read()

            if not ret:
                print(
                    f"Skipping {sec}s"
                )
                continue

            visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
            # Validate visual fingerprint dimension before adding to FAISS
            if len(visual_fp) != FAISS_VISUAL_DIM:
                logger.warning(
                    f"Visual fingerprint dimension mismatch ({len(visual_fp)} != {FAISS_VISUAL_DIM}); skipping FAISS visual add for segment {sec}s."
                )
                add_visual = False
            else:
                add_visual = True

            print(
                f"VISUAL SAMPLE @{sec}s:",
                visual_fp[:5]
            )

            start_audio = sec * sr

            end_audio = min(
                len(audio),
                (sec + SEGMENT_SECONDS) * sr
            )

            audio_chunk = audio[
                start_audio:end_audio
            ]

            audio_fp = (
                UnifiedFingerprinter
                .audio_fingerprint(
                    audio_chunk,
                    sr
                )
            )

            print(
                f"AUDIO SAMPLE @{sec}s:",
                audio_fp[:5]
            )

            db.add(
                ContentLibrary(
                    external_content_id=content_id,
                    title=title,
                    category=category,
                    channel_name=None,
                    segment_offset=sec,
                    visual_fp=json.dumps(
                        visual_fp
                    ),
                    audio_fp=json.dumps(
                        audio_fp
                    ),
                    logo_fp="[]",
                    ocr_keywords=""
                )
            )
            # Add fingerprints to FAISS indexes
            if add_visual:
                visual_vec = np.array(visual_fp, dtype=np.float32).reshape(1, -1)
                visual_index.add(visual_vec, [content_id])
            # Validate audio fingerprint dimension before adding to FAISS
            if len(audio_fp) != FAISS_AUDIO_DIM:
                logger.warning(
                    f"Audio fingerprint dimension mismatch ({len(audio_fp)} != {FAISS_AUDIO_DIM}); skipping FAISS audio add for segment {sec}s."
                )
                add_audio = False
            else:
                add_audio = True
            if add_audio:
                audio_vec = np.array(audio_fp, dtype=np.float32).reshape(1, -1)
                audio_index.add(audio_vec, [content_id])

            inserted += 1

            print(
                f"[{inserted}] Stored segment {sec}s"
            )

        print(
            f"Committing {inserted} segments..."
        )
        # Persist FAISS indexes after all segments are ingested
        visual_index.save(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH))
        audio_index.save(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH))

        db.commit()

        print(
            f"SUCCESS: {title}"
        )

    except Exception as e:

        db.rollback()

        print(
            f"FAILED: {title}"
        )

        raise e

    finally:

        try:
            cap.release()
        except Exception:
            pass

        db.close()


if __name__ == "__main__":

    print(
        "\nResetting database tables..."
    )

    Base.metadata.drop_all(
        bind=engine
    )

    Base.metadata.create_all(
        bind=engine
    )

    print(
        "Database ready."
    )

    reference_dir = (
        Path("manual_ingestion")
        / "reference_library"
    )

    print(
        f"Reference Directory = {reference_dir}"
    )

    if not reference_dir.exists():
        raise RuntimeError(
            f"Directory not found: {reference_dir}"
        )

    videos = list(
        reference_dir.glob("*.mp4")
    )

    if not videos:
        raise RuntimeError(
            "No MP4 files found."
        )

    print(
        f"Found {len(videos)} videos"
    )

    for video in videos:

        ingest_video(
            str(video),
            video.stem,
            "song"
        )

    print(
        "\nALL VIDEOS PROCESSED"
    )