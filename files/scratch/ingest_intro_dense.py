"""Re-ingest the Goyamart Intro with 2-second segment intervals for dense coverage."""
import json, os, sys, uuid
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import cv2
import librosa
import numpy as np
from content_platform.fingerprint.unified import UnifiedFingerprinter
from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor
from content_platform.server.db import SessionLocal
from content_platform.server.models import Content, ContentSegment

VIDEO_PATH = r"D:\Multimodel_Fingerprint\Intro\Goyamart - Intro.mp4"
TITLE = "Goyamart - Intro"
CONTENT_ID = "series-goyamart-intro"
SEGMENT_SECONDS = 2  # Dense: every 2 seconds

cap = cv2.VideoCapture(VIDEO_PATH)
fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
duration = int(frame_count / fps)

print(f"Video: {VIDEO_PATH}")
print(f"FPS: {fps:.1f}, Duration: {duration}s, Segment interval: {SEGMENT_SECONDS}s")

# Load audio
audio, sr = librosa.load(VIDEO_PATH, sr=16000, mono=True)
print(f"Audio loaded: {len(audio)} samples")

session = SessionLocal()
try:
    # Create content entry
    db_content = Content(
        content_id=CONTENT_ID,
        platform_id="global",
        title=TITLE,
        content_type="series",
        series_name="Goyamart",
        season_number=None,
        episode_number=None
    )
    session.add(db_content)
    session.flush()

    inserted = 0
    for sec in range(0, duration, SEGMENT_SECONDS):
        frame_number = int(sec * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
        ret, frame = cap.read()
        if not ret:
            continue

        # Visual fingerprint
        visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)

        # Audio fingerprint (10-second window centered on this segment)
        start_sample = max(0, sec * sr)
        end_sample = min(len(audio), (sec + 10) * sr)
        audio_chunk = audio[start_sample:end_sample]
        if len(audio_chunk) < 100:
            audio_chunk = np.zeros(sr * 10, dtype=np.float32)
        audio_fp = UnifiedFingerprinter.audio_fingerprint(audio_chunk, sr)

        segment_index = sec // SEGMENT_SECONDS
        db_item = ContentSegment(
            content_id=CONTENT_ID,
            segment_index=segment_index,
            segment_offset=sec,
            visual_fp=json.dumps(visual_fp),
            audio_fp=json.dumps(audio_fp)
        )
        session.add(db_item)
        inserted += 1
        print(f"  [{inserted}] Stored segment @ {sec}s")

    session.commit()
    print(f"\nDone! Ingested {inserted} dense segments for '{TITLE}'")
except Exception as e:
    session.rollback()
    print(f"Error: {e}")
    raise
finally:
    session.close()
    cap.release()
