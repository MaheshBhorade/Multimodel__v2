import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from content_platform.server.db import PlatformSessionLocal
from content_platform.server.models import PlatformReference

with PlatformSessionLocal() as db:
    items = db.query(PlatformReference).filter(PlatformReference.platform_id == "uplay").all()
    print("Found Uplay items in DB:", len(items))
    for item in items:
        print(f"ID: {item.id} | Platform ID: {item.platform_id} | Platform Name: {item.platform_name} | Type: {item.platform_type}")
