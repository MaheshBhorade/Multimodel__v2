import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

import json
from content_platform.server.db import SessionLocal
from content_platform.server.models import Capture, RecognitionResultRecord, Device
from content_platform.server.matching import match_content, initialize_faiss_indexes
from content_platform.shared.models import FingerprintPayload

with SessionLocal() as db:
    initialize_faiss_indexes(db)
    cap = db.query(Capture).filter(Capture.id == 49972).first()
    
    device = db.query(Device).filter(Device.device_id == "pi-agent").first()
    last_record = (
        db.query(RecognitionResultRecord)
        .join(Capture, Capture.id == RecognitionResultRecord.capture_id)
        .filter(Capture.device_id == device.id)
        .filter(Capture.captured_at < cap.captured_at) # get last record before this capture
        .order_by(Capture.captured_at.desc())
        .first()
    )
    if last_record:
        print("Last Record in DB before 49972:", last_record.content_name, "ID:", last_record.content_id, "Time:", last_record.capture.captured_at)
    else:
        print("No Last Record found")
        
    single_payload = FingerprintPayload(
        device_id="pi-agent",
        timestamp=cap.captured_at,
        visual_fps=json.loads(cap.visual_fp),
        audio_fp=[0.0] * 130,
        snapshot_url=cap.snapshot_url,
        batch_count=1,
        batch_duration_sec=1,
        best_frame_index=0,
        confidence_visual=0.5,
        confidence_audio=0.5
    )
    
    # Run matching and print details
    result = match_content(db, single_payload)
    print("Match Name:", result.content_name)
    print("Match Confidence:", result.confidence)
