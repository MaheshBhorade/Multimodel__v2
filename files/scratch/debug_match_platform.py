import json
import sys
import os
from pathlib import Path

# Add src to python path
sys.path.append(os.path.abspath("src"))

from content_platform.server.db import SessionLocal, PlatformSessionLocal
from content_platform.server.models import Capture
from content_platform.shared.models import FingerprintPayload
from content_platform.server.matching import match_platform

db = SessionLocal()
db_platform = PlatformSessionLocal()

# Find the capture corresponding to the YouTube Music snapshot
capture = db.query(Capture).filter(Capture.snapshot_url.like("%1781776116%")).first()

if not capture:
    print("Target capture not found!")
    sys.exit(1)

payload = FingerprintPayload(
    device_id=capture.device.device_id,
    timestamp=capture.captured_at,
    visual_fps=json.loads(capture.visual_fp),
    audio_fp=json.loads(capture.audio_fp),
    snapshot_url=capture.snapshot_url
)

print("Capture ID:", capture.id)
print("Snapshot URL:", capture.snapshot_url)

# Search platform
plat = match_platform(db_platform, payload)
print("Matched Platform ID:", plat)

# Detailed scores
from content_platform.server.models import PlatformReference
from content_platform.server.matching import cosine_like_similarity

print("\n--- Detailed Platform Scores ---")
items = db_platform.query(PlatformReference).all()
scores = {}
for item in items:
    try:
        lib_visual = json.loads(item.visual_fp)
    except Exception:
        continue
        
    first_frame = payload.visual_fps[0] if isinstance(payload.visual_fps[0], list) else payload.visual_fps
    sim = cosine_like_similarity(first_frame, lib_visual)
    
    if item.platform_id not in scores or sim > scores[item.platform_id]:
        scores[item.platform_id] = sim

for pid, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
    print(f"Platform: {pid} | Score: {score}")

db.close()
db_platform.close()
