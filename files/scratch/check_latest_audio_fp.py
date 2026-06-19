import json
from content_platform.server.db import SessionLocal
from content_platform.server.models import Capture

with SessionLocal() as db:
    captures = db.query(Capture).order_by(Capture.captured_at.desc()).limit(10).all()
    print("Latest 10 captures audio fingerprint status:")
    for c in captures:
        try:
            aud = json.loads(c.audio_fp)
        except Exception:
            aud = []
        is_zero = all(abs(x) < 1e-6 for x in aud) if aud else True
        preview = str(aud[:5]) if aud else "[]"
        print(f"Capture ID: {c.id} | Time: {c.captured_at} | Status: {c.status} | Audio FP Len: {len(aud)} | All zeros: {is_zero} | Preview: {preview}")
