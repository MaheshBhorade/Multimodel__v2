from content_platform.server.db import SessionLocal
from content_platform.server.models import ContentSegment, Content
from sqlalchemy import select, func
import json
import time

db = SessionLocal()
start = time.time()

contents = db.query(Content).all()
content_map = {c.content_id: c for c in contents}

min_ids = db.scalars(select(func.min(ContentSegment.segment_id)).group_by(ContentSegment.content_id)).all()

segments = db.execute(
    select(
        ContentSegment.segment_id,
        ContentSegment.content_id,
        ContentSegment.visual_fp,
        ContentSegment.audio_fp
    ).where(ContentSegment.segment_id.in_(min_ids))
).all()

print(f"Query completed in {time.time() - start:.3f}s")
start_parse = time.time()

results = []
for seg_id, content_id, visual_fp_str, audio_fp_str in segments:
    content = content_map.get(content_id)
    if not content:
        continue
    try:
        prefix = visual_fp_str[:120].rsplit(',', 1)[0] + ']'
        visual_fp = json.loads(prefix)[:4]
    except Exception:
        try:
            visual_fp = json.loads(visual_fp_str)[:4] if visual_fp_str else []
        except Exception:
            visual_fp = []
            
    try:
        prefix = audio_fp_str[:120].rsplit(',', 1)[0] + ']'
        audio_fp = json.loads(prefix)[:4]
    except Exception:
        try:
            audio_fp = json.loads(audio_fp_str)[:4] if audio_fp_str else []
        except Exception:
            audio_fp = []
            
    results.append({
        "id": seg_id,
        "external_content_id": content_id,
        "title": content.title,
        "category": content.content_type,
        "channel_name": content.platform_id,
        "visual_fp": visual_fp,
        "logo_fp": [],
        "audio_fp": audio_fp,
        "ocr_keywords": "",
        "platform": content.platform_id,
        "series": content.series_name,
        "season": content.season_number,
        "episode": content.episode_number,
    })

print(f"Parsing completed in {time.time() - start_parse:.3f}s")
print(f"Total time: {time.time() - start:.3f}s")
print(f"Returned {len(results)} items")
if results:
    print("Example result:", results[0])
