import json
from collections import Counter
from content_platform.server.db import SessionLocal
from content_platform.server.models import ContentSegment

with SessionLocal() as db:
    segments = db.query(ContentSegment).all()
    lengths = []
    types = []
    for s in segments:
        try:
            val = json.loads(s.audio_fp)
            lengths.append(len(val) if isinstance(val, list) else -1)
            types.append(type(val).__name__)
        except Exception as e:
            lengths.append(-2)
            types.append(type(s.audio_fp).__name__)
            
    print("Audio FP length distribution:", Counter(lengths))
    print("Audio FP type distribution:", Counter(types))
