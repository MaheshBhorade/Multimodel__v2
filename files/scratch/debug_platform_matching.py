import sys
import os
import json
from pathlib import Path

# Add project root and src subdirectory to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from content_platform.server.db import SessionLocal, PlatformSessionLocal
from content_platform.server.models import Capture, PlatformLibrary
from content_platform.server.matching import match_platform
from content_platform.shared.models import FingerprintPayload


def debug_platform_matching():
    print("=== Database Status ===")
    with SessionLocal() as db:
        cap_count = db.query(Capture).count()
        print(f"Content Captures count: {cap_count}")
        
    with PlatformSessionLocal() as db_platform:
        plat_count = db_platform.query(PlatformLibrary).count()
        print(f"Platform Layout Reference count: {plat_count}")
        
        if plat_count == 0:
            print("ERROR: platform_library.db is empty!")
            return
            
        # Let's list some platform entries in the DB
        platforms = db_platform.query(PlatformLibrary.platform_name).distinct().all()
        print(f"Platforms in DB: {[p[0] for p in platforms]}")
        
        # Get last 5 captures
        with SessionLocal() as db:
            latest_captures = db.query(Capture).order_by(Capture.id.desc()).limit(5).all()
            if not latest_captures:
                print("No captures found in main DB to test matching.")
                return
                
            print("\n=== Matching Details for Latest Captures ===")
            for cap in latest_captures:
                print(f"\nCapture ID: {cap.id} (captured_at: {cap.captured_at})")
                try:
                    vis_fps = json.loads(cap.visual_fp)
                except Exception as e:
                    print(f"  Error parsing visual_fp: {e}")
                    continue
                    
                payload = FingerprintPayload(
                    device_id="DEBUG",
                    timestamp=cap.captured_at,
                    visual_fps=vis_fps,
                    audio_fp=[]
                )
                
                # Run matching manually to see scores
                # We copy the match_platform logic here to print scores of all candidates
                from content_platform.server.matching import is_blank_signal, cosine_like_similarity
                
                first_frame = payload.visual_fps[0] if isinstance(payload.visual_fps[0], list) else payload.visual_fps
                if is_blank_signal(first_frame):
                    print("  Capture is blank/lost visual signal.")
                    continue
                    
                items = db_platform.query(PlatformLibrary).all()
                matches = []
                for item in items:
                    try:
                        lib_visual = json.loads(item.visual_fp)
                    except Exception:
                        continue
                        
                    if payload.visual_fps and isinstance(payload.visual_fps[0], (int, float)):
                        sim = cosine_like_similarity(payload.visual_fps, lib_visual)
                    else:
                        similarities = [
                            cosine_like_similarity(fp, lib_visual)
                            for fp in payload.visual_fps
                        ]
                        sim = max(similarities) if similarities else 0.0
                        
                    matches.append({"platform": item.platform_name, "score": sim})
                
                if not matches:
                    print("  No layout references in database matched.")
                    continue
                    
                matches.sort(key=lambda x: x["score"], reverse=True)
                
                print("  Top 5 Platform Matches:")
                seen = set()
                count = 0
                for m in matches:
                    if m["platform"] not in seen:
                        seen.add(m["platform"])
                        print(f"    - {m['platform']}: Score = {m['score']:.4f}")
                        count += 1
                        if count >= 5:
                            break
                            
                # Print the final result from the actual engine
                final_plat = match_platform(db_platform, payload)
                print(f"  Result Platform: {final_plat}")


if __name__ == "__main__":
    debug_platform_matching()
