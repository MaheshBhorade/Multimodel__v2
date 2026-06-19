import sys
import os
import json
import cv2
import numpy as np
from pathlib import Path

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from content_platform.server.db import PlatformSessionLocal
from content_platform.server.models import PlatformLibrary
from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor
from content_platform.server.matching import match_platform
from content_platform.shared.models import FingerprintPayload

def test_real_match():
    # Find a platform frame
    platform_dir = Path("platform/frames")
    if not platform_dir.exists():
        print("platform/frames directory not found.")
        return
        
    # Find first image in Uplay or Youtube
    img_path = None
    platform_name = None
    for subdir in platform_dir.iterdir():
        if subdir.is_dir():
            for f in subdir.iterdir():
                if f.suffix.lower() in (".png", ".jpg", ".jpeg"):
                    img_path = f
                    platform_name = subdir.name
                    break
            if img_path:
                break
                
    if not img_path:
        print("No image found in platform/frames subdirectories.")
        return
        
    print(f"Testing image: {img_path} (Expected Platform: {platform_name})")
    
    frame = cv2.imread(str(img_path))
    if frame is None:
        print("Failed to load frame.")
        return
        
    # Extract visual fingerprint
    vis_fp = DeepEmbeddingsExtractor.extract_visual(frame)
    print("Fingerprint length:", len(vis_fp))
    print("Fingerprint sample:", vis_fp[:10])
    
    from datetime import datetime, UTC
    # Create payload
    payload = FingerprintPayload(
        device_id="TEST",
        timestamp=datetime.now(UTC),
        visual_fps=[vis_fp],
        audio_fp=[]
    )
    
    with PlatformSessionLocal() as db_platform:
        # Check FAISS index details
        import content_platform.server.matching as m_mod
        m_mod.initialize_platform_faiss_index(db_platform)
        print("FAISS index in module is None?", m_mod.platform_visual_index is None)
        if m_mod.platform_visual_index is not None:
            print("FAISS index dim:", m_mod.platform_visual_index.dim)
            print("FAISS index size:", len(m_mod.platform_visual_index.id_to_content))
            
        # Re-run match_platform logic step by step
        print("\n--- Dry Run match_platform ---")
        first_frame = payload.visual_fps[0] if isinstance(payload.visual_fps[0], list) else payload.visual_fps
        print("Is blank?", m_mod.is_blank_signal(first_frame))
        
        use_faiss = False
        if m_mod.platform_visual_index is not None:
            if isinstance(payload.visual_fps[0], (list, tuple, np.ndarray)):
                payload_vis_dim = len(payload.visual_fps[0])
            else:
                payload_vis_dim = len(payload.visual_fps)
            print("payload_vis_dim:", payload_vis_dim)
            if payload_vis_dim == m_mod.platform_visual_index.dim:
                use_faiss = True
        print("use_faiss:", use_faiss)
        
        matches = []
        if use_faiss:
            frames_to_search = payload.visual_fps if isinstance(payload.visual_fps[0], list) else [payload.visual_fps]
            visual_scores = {}
            for frame in frames_to_search:
                frame_arr = np.array(frame, dtype=np.float32)
                visual_results = m_mod.platform_visual_index.search(frame_arr, k=5)
                print("FAISS Search results for frame:", visual_results)
                for platform_name, dist in visual_results:
                    sim = 1 / (1 + dist)
                    if platform_name not in visual_scores or sim > visual_scores[platform_name]:
                        visual_scores[platform_name] = sim
            for platform_name, score in visual_scores.items():
                matches.append({"platform": platform_name, "score": score})
        else:
            items = db_platform.query(PlatformLibrary).all()
            print("Brute force items count:", len(items))
            for item in items:
                try:
                    lib_visual = json.loads(item.visual_fp)
                except Exception:
                    continue
                if payload.visual_fps and isinstance(payload.visual_fps[0], (int, float)):
                    sim = m_mod.cosine_like_similarity(payload.visual_fps, lib_visual)
                else:
                    similarities = [
                        m_mod.cosine_like_similarity(fp, lib_visual)
                        for fp in payload.visual_fps
                    ]
                    sim = max(similarities) if similarities else 0.0
                matches.append({"platform": item.platform_name, "score": sim})
                
        print("Matches found count:", len(matches))
        if matches:
            matches.sort(key=lambda x: x["score"], reverse=True)
            print("Top 5 matches in dry run:")
            for m in matches[:5]:
                print(f"  - {m}")
            best_match = matches[0]
            print("Best match:", best_match)
            if best_match["score"] >= 0.45:
                print("Result would be:", best_match["platform"])
            else:
                print("Result would be: unknown (below 0.45)")
        else:
            print("Result would be: unknown (no matches)")
            
        matched = m_mod.match_platform(db_platform, payload)
        print("\nActual match_platform result:", matched)
        
        # Print actual scores
        from content_platform.server.matching import cosine_like_similarity
        items = db_platform.query(PlatformLibrary).all()
        scores = []
        for item in items:
            lib_vis = json.loads(item.visual_fp)
            sim = cosine_like_similarity(vis_fp, lib_vis)
            scores.append((item.platform_name, item.segment_offset, sim))
            
        scores.sort(key=lambda x: x[2], reverse=True)
        print("\nTop 5 Scores:")
        for plat, offset, sim in scores[:5]:
            print(f"  - {plat} (offset {offset}s): {sim:.4f}")

if __name__ == "__main__":
    test_real_match()
