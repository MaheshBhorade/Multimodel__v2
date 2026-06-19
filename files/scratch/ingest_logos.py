import json
import os
import sys
import re
from pathlib import Path
import numpy as np
import cv2

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from content_platform.server.db import PlatformSessionLocal
from content_platform.server.models import PlatformReference
from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor
from content_platform.server.faiss_index import FAISSIndex

LOGO_FP_DIR = Path(r"D:\Multimodel_Fingerprint\logo_fp")
FAISS_INDEX_DIR = Path("runtime/faiss")
PLATFORM_VISUAL_INDEX_PATH = FAISS_INDEX_DIR / "platform_visual.index"
PLATFORM_VISUAL_META_PATH = FAISS_INDEX_DIR / "platform_visual_meta.json"

FAISS_VISUAL_DIM = 960

def slugify(text: str) -> str:
    return re.sub(r'[-\s]+', '_', text.lower().strip())

def main():
    if not LOGO_FP_DIR.exists():
        print(f"Directory {LOGO_FP_DIR} does not exist. Please create it first.", flush=True)
        sys.exit(1)

    # Load platform FAISS index
    if PLATFORM_VISUAL_INDEX_PATH.exists() and PLATFORM_VISUAL_META_PATH.exists():
        print("Loading existing platform FAISS index...", flush=True)
        platform_idx = FAISSIndex.load(str(PLATFORM_VISUAL_INDEX_PATH), str(PLATFORM_VISUAL_META_PATH), dim=FAISS_VISUAL_DIM, gpu=True)
    else:
        print("Initializing new platform FAISS index...", flush=True)
        platform_idx = FAISSIndex(dim=FAISS_VISUAL_DIM, gpu=True)

    db_platform = PlatformSessionLocal()
    
    # Supported image extensions
    img_extensions = {".png", ".jpg", ".jpeg", ".webp"}
    
    try:
        # Scan logo_fp directory for channel folders (e.g. logo_fp/PanArmenian TV)
        channel_folders = [d for d in LOGO_FP_DIR.iterdir() if d.is_dir()]
        
        if not channel_folders:
            print("No channel directories found under logo_fp/.", flush=True)
            return

        for folder in channel_folders:
            channel_name = folder.name
            platform_id = slugify(channel_name)
            
            # Find all images recursively inside this channel folder
            img_files = []
            for ext in img_extensions:
                img_files.extend(list(folder.rglob(f"*{ext}")))
                img_files.extend(list(folder.rglob(f"*{ext.upper()}")))

            if not img_files:
                print(f"No images found for channel '{channel_name}' in folder: {folder}", flush=True)
                continue

            print(f"Processing channel: {channel_name} (ID: {platform_id}) with {len(img_files)} images.", flush=True)

            for img_path in sorted(img_files):
                frame = cv2.imread(str(img_path))
                if frame is None:
                    print(f"  Failed to read image: {img_path.name}", flush=True)
                    continue

                height, width = frame.shape[:2]
                
                # Check if it's a full screenshot (requiring ROI crop) or already a cropped logo
                # If width > 400, crop the bottom-right corner ROI
                if width > 400:
                    ymin, ymax = int(height * 0.75), int(height * 0.95)
                    xmin, xmax = int(width * 0.75), int(width * 0.98)
                    logo_img = frame[ymin:ymax, xmin:xmax]
                    print(f"  Cropping bottom-right ROI from full screenshot: {img_path.name}", flush=True)
                else:
                    logo_img = frame
                    print(f"  Using image as-is (already cropped logo): {img_path.name}", flush=True)

                # Extract embedding
                visual_fp = DeepEmbeddingsExtractor.extract_visual(logo_img)
                
                # Save to DB
                db_item = PlatformReference(
                    platform_id=platform_id,
                    platform_name=channel_name,
                    platform_type="TV",  # Default to TV (channels)
                    visual_fp=json.dumps(visual_fp),
                    logo_fp=json.dumps(visual_fp),
                    ocr_keywords=""
                )
                db_platform.add(db_item)
                db_platform.flush()

                # Add to FAISS index
                meta = {
                    "platform_id": platform_id,
                    "platform_name": channel_name,
                    "platform_type": "TV"
                }
                platform_idx.add(np.array(visual_fp, dtype=np.float32).reshape(1, -1), [meta])
                print(f"  Successfully ingested: {img_path.name}", flush=True)

        db_platform.commit()
        
        # Save FAISS index
        print("Saving platform FAISS index to disk...", flush=True)
        platform_idx.save(str(PLATFORM_VISUAL_INDEX_PATH), str(PLATFORM_VISUAL_META_PATH))
        print("Platform logo ingestion completed successfully!", flush=True)

    except Exception as e:
        db_platform.rollback()
        print(f"Error occurred: {e}", flush=True)
        raise
    finally:
        db_platform.close()

if __name__ == "__main__":
    main()
