import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from content_platform.server.db import PlatformSessionLocal
from content_platform.server.models import PlatformReference

with PlatformSessionLocal() as db:
    items = db.query(PlatformReference).all()
    platforms = set((item.platform_id, item.platform_name) for item in items)
    print("Available Platforms:")
    for pid, pname in platforms:
        print(f"ID: {pid} | Name: {pname}")
