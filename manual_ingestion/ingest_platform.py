import argparse
import json
import os
import sys
from pathlib import Path

# Add project root and src directory to sys.path to allow imports of content_platform
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

# Import project modules
try:
    import cv2
    import numpy as np
except ImportError:
    print("Error: 'opencv-python' is not installed in the current environment.")
    sys.exit(1)

try:
    from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor
except ImportError:
    print("Error: DeepEmbeddingsExtractor is not available. Ensure PYTHONPATH is correct.")
    sys.exit(1)

try:
    from content_platform.server.db import PlatformSessionLocal
    from content_platform.server.models import PlatformReference
except ImportError:
    print("Error: Platform database engine and model classes could not be imported.")
    sys.exit(1)


def get_platform_info(platform_name: str):
    platform_id = platform_name.lower().replace(" ", "_").replace("-", "_")
    info = {
        "netflix": ("Netflix", "OTT"),
        "youtube": ("YouTube", "OTT"),
        "prime_video": ("Prime Video", "OTT"),
        "sonyliv": ("SonyLIV", "OTT"),
        "jiohotstar": ("JioHotstar", "OTT"),
        "zee5": ("Zee5", "OTT"),
        "sony_max": ("Sony Max", "TV"),
        "star_gold": ("Star Gold", "TV"),
        "zee_cinema": ("Zee Cinema", "TV")
    }
    name, ptype = info.get(platform_id, (platform_name, "OTT"))
    return platform_id, name, ptype


def ingest_single_image(image_path: str, platform_name: str, offset: int = 0, session=None):
    close_session = False
    if session is None:
        session = PlatformSessionLocal()
        close_session = True
        
    try:
        frame = cv2.imread(image_path)
        if frame is None:
            raise RuntimeError(f"Could not load image: {image_path}")
            
        visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
        platform_id, plat_name, plat_type = get_platform_info(platform_name)
        
        db_item = PlatformReference(
            platform_id=platform_id,
            platform_name=plat_name,
            platform_type=plat_type,
            visual_fp=json.dumps(visual_fp),
            ocr_keywords=""
        )
        session.add(db_item)
        if close_session:
            session.commit()
            print(f"Ingested image: {image_path} -> {plat_name} (ID: {platform_id})")
    except Exception as e:
        if close_session:
            session.rollback()
        raise e
    finally:
        if close_session:
            session.close()


def ingest_platform_video(video_path: str, platform_name: str, interval: int = 10, session=None):
    close_session = False
    if session is None:
        session = PlatformSessionLocal()
        close_session = True
        
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
        
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or frame_count <= 0:
        raise RuntimeError(f"Invalid FPS ({fps}) or Frame Count ({frame_count})")
        
    duration = int(frame_count / fps)
    print(f"Video FPS: {fps:.2f}, Duration: {duration} seconds")
    
    inserted = 0
    platform_id, plat_name, plat_type = get_platform_info(platform_name)
    try:
        for sec in range(0, duration, interval):
            frame_number = int(sec * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
            ret, frame = cap.read()
            if not ret:
                continue
                
            visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
            
            db_item = PlatformReference(
                platform_id=platform_id,
                platform_name=plat_name,
                platform_type=plat_type,
                visual_fp=json.dumps(visual_fp),
                ocr_keywords=""
            )
            session.add(db_item)
            inserted += 1
            print(f"  [{inserted}] Ingested segment @ {sec}s")
            
        if close_session:
            session.commit()
            print(f"Committed {inserted} video signatures to database!")
        return inserted
    except Exception as e:
        if close_session:
            session.rollback()
        raise e
    finally:
        cap.release()
        if close_session:
            session.close()


def main():
    parser = argparse.ArgumentParser(description="Ingest platform UI/layout videos or images into the platform library.")
    parser.add_argument("--input", "-i", required=True, help="Path to video file, image file, or directory")
    parser.add_argument("--platform", "-p", default=None, help="Platform name (e.g. YouTube, Netflix, Hotstar). Optional if input directory has subdirectories.")
    parser.add_argument("--interval", "-t", type=int, default=10, help="Sampling interval in seconds for videos (default: 10)")
    
    args = parser.parse_args()
    
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Path does not exist at '{args.input}'")
        sys.exit(1)
        
    session = PlatformSessionLocal()
    try:
        # Determine platform name(s) and paths
        targets = []
        if args.platform is None:
            if input_path.is_file():
                print("Error: A platform name must be specified using --platform when ingesting a single file.")
                sys.exit(1)
            
            subdirs = [d for d in input_path.iterdir() if d.is_dir()]
            if not subdirs:
                print("Error: A platform name must be specified using --platform, or the input directory must contain subdirectories representing platforms.")
                sys.exit(1)
                
            for subdir in subdirs:
                targets.append((subdir, subdir.name))
        else:
            targets.append((input_path, args.platform))
            
        for path_to_process, platform_name in targets:
            print(f"\nProcessing target: {path_to_process.name} -> Platform: {platform_name}")
            if path_to_process.is_file():
                suffix = path_to_process.suffix.lower()
                if suffix in (".png", ".jpg", ".jpeg", ".bmp", ".webp"):
                    ingest_single_image(str(path_to_process), platform_name, 0, session)
                elif suffix in (".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm"):
                    ingest_platform_video(str(path_to_process), platform_name, args.interval, session)
            elif path_to_process.is_dir():
                video_suffixes = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm")
                image_suffixes = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
                
                offset = 0
                for child in sorted(path_to_process.iterdir()):
                    if child.is_file():
                        suffix = child.suffix.lower()
                        if suffix in image_suffixes:
                            ingest_single_image(str(child), platform_name, offset, session)
                            offset += args.interval
                        elif suffix in video_suffixes:
                            print(f"  Processing video file: {child.name}")
                            ingest_platform_video(str(child), platform_name, args.interval, session)
                            
        session.commit()
        print("\nIngestion complete!")
    except Exception as e:
        session.rollback()
        print(f"Ingestion failed: {e}")
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    main()
