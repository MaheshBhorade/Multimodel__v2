import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from content_platform.server.db import SessionLocal
from content_platform.server.models import Content

with SessionLocal() as db:
    items = db.query(Content).filter(Content.title.like("%MI GEXECIK OR%")).all()
    print("Found items:", len(items))
    for item in items[:10]:
        print(f"ID: {item.content_id} | Title: {item.title} | Platform: {item.platform_id} | Series: {item.series_name}")
