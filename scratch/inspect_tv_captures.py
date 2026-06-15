import sys
import os
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from content_platform.server.db import SessionLocal
from content_platform.server.models import Capture, RecognitionResultRecord, Device

def inspect_tv_captures():
    db = SessionLocal()
    # Get captures that are NOT Goyamart
    res_records = db.query(RecognitionResultRecord).filter(
        RecognitionResultRecord.content_name == "Unknown Content"
    ).order_by(RecognitionResultRecord.id.desc()).limit(20).all()
    
    print(f"Found {len(res_records)} Unknown Content records.")
    for r in res_records:
        cap = db.query(Capture).filter(Capture.id == r.capture_id).first()
        if not cap:
            continue
        vis = json.loads(cap.visual_fp)
        first_frame = vis[0] if isinstance(vis[0], list) else vis
        print(f"Capture ID: {cap.id} | Device: {cap.device.device_id} | Matched Platform: {r.matched_platform}")
        print(f"  First 10 values: {first_frame[:10]}")

if __name__ == "__main__":
    inspect_tv_captures()
