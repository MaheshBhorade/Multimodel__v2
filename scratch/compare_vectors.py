import sys
import os
import json
from pathlib import Path

# Add project root and src subdirectory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from content_platform.server.db import SessionLocal, PlatformSessionLocal
from content_platform.server.models import Capture, PlatformLibrary


def compare_vectors():
    with SessionLocal() as db:
        cap = db.query(Capture).order_by(Capture.id.desc()).first()
        if cap:
            print("=== Capture Visual Fingerprint ===")
            vis = json.loads(cap.visual_fp)
            print(f"Type: {type(vis)}")
            if isinstance(vis, list):
                print(f"Outer list len: {len(vis)}")
                if len(vis) > 0 and isinstance(vis[0], list):
                    print(f"Inner list len: {len(vis[0])}")
                    print(f"Sample values (first 10 of first frame): {vis[0][:10]}")
                else:
                    print(f"Sample values (first 10): {vis[:10]}")
            
    with PlatformSessionLocal() as db_platform:
        item = db_platform.query(PlatformLibrary).first()
        if item:
            print("\n=== Platform Library Visual Fingerprint ===")
            vis = json.loads(item.visual_fp)
            print(f"Type: {type(vis)}")
            if isinstance(vis, list):
                print(f"Outer list len: {len(vis)}")
                if len(vis) > 0 and isinstance(vis[0], list):
                    print(f"Inner list len: {len(vis[0])}")
                    print(f"Sample values (first 10 of first frame): {vis[0][:10]}")
                else:
                    print(f"Sample values (first 10): {vis[:10]}")


if __name__ == "__main__":
    compare_vectors()
