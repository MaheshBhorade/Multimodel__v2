import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from content_platform.server.db import SessionLocal
from content_platform.server.models import Capture, RecognitionResultRecord

with SessionLocal() as db:
    cap = db.query(Capture).filter(Capture.id == 49996).first()
    print("Capture ID:", cap.id)
    print("Captured At:", cap.captured_at)
    print("Visual FP (first frame representation or length):", len(cap.visual_fp))
    
    rec = db.query(RecognitionResultRecord).filter(RecognitionResultRecord.capture_id == 49996).first()
    if rec:
        print("Record ID:", rec.id)
        print("Content Name:", rec.content_name)
        print("Confidence:", rec.confidence)
        print("Visual Score:", rec.visual_score)
        print("Audio Score:", rec.audio_score)
        print("Matched Platform:", rec.matched_platform)
        print("Playback Position:", rec.playback_position)
    else:
        print("No RecognitionResultRecord found for capture 49996")
