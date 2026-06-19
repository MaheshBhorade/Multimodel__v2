import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

import json
from datetime import datetime, UTC
from content_platform.server.db import SessionLocal
from content_platform.server.models import Content, ContentSegment
from content_platform.server.matching import match_content, initialize_faiss_indexes
from content_platform.shared.models import FingerprintPayload

with SessionLocal() as db:
    initialize_faiss_indexes(db)
    segment = next(item for item in db.query(ContentSegment).join(Content, Content.content_id == ContentSegment.content_id) if any(x != 0.0 for x in json.loads(item.visual_fp)))
    print("Segment ID:", segment.segment_id)
    print("Content Title:", segment.content.title)
    
    ref_visual = json.loads(segment.visual_fp)
    ref_audio = json.loads(segment.audio_fp)
    
    payload = FingerprintPayload(
        device_id="PI001",
        timestamp=datetime.now(UTC),
        visual_fps=[ref_visual] * 10,
        audio_fp=ref_audio,
        snapshot_url="http://localhost/snapshot.jpg",
    )
    
    result = match_content(db, payload)
    print("Result Name:", result.content_name)
    print("Result Confidence:", result.confidence)
    print("Result Breakdown:", result.breakdown)
