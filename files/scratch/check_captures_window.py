import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from datetime import datetime
from content_platform.server.db import SessionLocal
from content_platform.server.models import Capture, RecognitionResultRecord

with SessionLocal() as db:
    caps = db.query(Capture).filter(
        Capture.captured_at >= datetime(2026, 6, 19, 5, 49, 20),
        Capture.captured_at <= datetime(2026, 6, 19, 5, 49, 40)
    ).all()
    for cap in caps:
        rec = db.query(RecognitionResultRecord).filter(RecognitionResultRecord.capture_id == cap.id).first()
        if rec:
            print(f"Time: {cap.captured_at.time()} | ID: {cap.id} | Content: {rec.content_name} | Conf: {rec.confidence:.2f} | Vis: {rec.visual_score:.2f} | Aud: {rec.audio_score:.2f}")
        else:
            print(f"Time: {cap.captured_at.time()} | ID: {cap.id} | No Record")
