import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

import json
from content_platform.server.db import SessionLocal, PlatformSessionLocal
from content_platform.server.models import Content, PlatformReference

# 1. Update Content library platform_id
with SessionLocal() as db:
    items = db.query(Content).filter(Content.title.like("%MI GEXECIK OR%")).all()
    print(f"Found {len(items)} Content items to update.")
    for item in items:
        item.platform_id = "shant_tv_armenia"
    db.commit()
    print("Content library updated successfully.")

# 2. Add Platform Reference for SHANT TV Armenia
with PlatformSessionLocal() as db_platform:
    exists = db_platform.query(PlatformReference).filter(PlatformReference.platform_id == "shant_tv_armenia").first()
    if not exists:
        dummy_fp = [0.0] * 960
        new_ref = PlatformReference(
            platform_id="shant_tv_armenia",
            platform_name="SHANT TV Armenia",
            platform_type="TV",
            visual_fp=json.dumps(dummy_fp),
            logo_fp=json.dumps([0.0] * 960),
            ocr_keywords=""
        )
        db_platform.add(new_ref)
        db_platform.commit()
        print("Added PlatformReference for SHANT TV Armenia.")
    else:
        print("PlatformReference for SHANT TV Armenia already exists.")
