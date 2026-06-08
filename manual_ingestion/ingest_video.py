import argparse
import json
import os
import subprocess
import struct
import sys
import tempfile

# Add project root to sys.path to allow imports of content_platform
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Try importing dependencies
try:
    import cv2
    import numpy as np
except ImportError:
    print("Warning: 'opencv-python' is not installed in the current environment.")
    print("Please install it using: .venv\\Scripts\\pip install opencv-python")
    cv2 = None

try:
    import httpx
except ImportError:
    httpx = None


def extract_audio_fingerprint(video_path: str, start_time_sec: float) -> str:
    """Extracts a 10-second mono 16kHz S16_LE WAV snippet from the video,
    then computes the volume-invariant audio fingerprint.
    """
    temp_wav = tempfile.mktemp(suffix=".wav")
    try:
        # ffmpeg -y -ss <start_time> -i <video_path> -t 10 -f wav -acodec pcm_s16le -ac 1 -ar 16000 <temp_wav>
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_time_sec),
            "-i", video_path,
            "-t", "10",
            "-f", "wav",
            "-acodec", "pcm_s16le",
            "-ac", "1",
            "-ar", "16000",
            temp_wav
        ]
        # Run ffmpeg
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        
        if not os.path.exists(temp_wav):
            raise RuntimeError(f"FFmpeg failed to produce the wav file. Stderr: {result.stderr.decode()}")
            
        with open(temp_wav, "rb") as f:
            audio_bytes = f.read()
            
        # Parse audio_bytes
        header_size = 44
        raw_data = audio_bytes[header_size:]
        num_samples = len(raw_data) // 2
        if num_samples < 100:
            raise ValueError("Extracted audio snippet is too short.")
            
        samples = struct.unpack(f"<{num_samples}h", raw_data)
        
        # Divide into 10 buckets
        bucket_size = len(samples) // 10
        energies = []
        for i in range(10):
            bucket = samples[i * bucket_size: (i + 1) * bucket_size]
            energy = sum(abs(s) for s in bucket) / len(bucket) if bucket else 0.0
            energies.append(energy)
            
        max_energy = max(energies) if energies else 0.0
        if max_energy > 0:
            normalized = [int((e / max_energy) * 100) for e in energies]
        else:
            normalized = [0] * 10
            
        return "ae-hash-" + "-".join(str(e) for e in normalized)
    finally:
        if os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except OSError:
                pass


def extract_visual_fingerprints(video_path: str, start_time_sec: float) -> tuple[list[float], list[float]]:
    """Extracts a frame from the video at start_time_sec,
    and computes the 64-dim visual fingerprint and 16-dim logo fingerprint.
    """
    if cv2 is None:
        raise RuntimeError("OpenCV (cv2) is required to extract visual fingerprints from the video.")
        
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video file: {video_path}")
        
    # Get frame rate and skip to the frame at start_time_sec
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = 25.0
    frame_no = int(start_time_sec * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_no)
    
    ok, frame = cap.read()
    if not ok:
        # Fall back to frame 0
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"Failed to read any frame from: {video_path}")
            
    cap.release()
    
    # 1. 64-dim visual fingerprint
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
    normalized = resized / 255.0
    visual_fp = [round(float(val), 4) for val in normalized.flatten().tolist()]
    
    # 2. 16-dim logo fingerprint (top-right corner 25% height, 25% width)
    h, w = frame.shape[:2]
    crop_h = max(1, h // 4)
    crop_w = max(1, w // 4)
    logo_crop = frame[0:crop_h, w - crop_w:w]
    
    logo_gray = cv2.cvtColor(logo_crop, cv2.COLOR_BGR2GRAY)
    logo_resized = cv2.resize(logo_gray, (4, 4), interpolation=cv2.INTER_AREA)
    logo_normalized = logo_resized / 255.0
    logo_fp = [round(float(val), 4) for val in logo_normalized.flatten().tolist()]
    
    return visual_fp, logo_fp


def main():
    parser = argparse.ArgumentParser(description="Ingest video files into the content reference library.")
    parser.add_argument("--video", "-v", required=True, help="Path to the video file")
    parser.add_argument("--title", "-t", required=True, help="Title of the content")
    parser.add_argument("--category", "-c", default="music", choices=["movie", "series", "advertisement", "music", "channel"], help="Category of the content")
    parser.add_argument("--channel", "-n", default="Local Library", help="Associated channel name")
    parser.add_argument("--ocr-keywords", "-o", default="", help="OCR keywords associated (defaults to title)")
    parser.add_argument("--start-time", "-s", type=float, default=10.0, help="Start time in seconds to extract signatures (default: 10s)")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/api/v1/library", help="Local server API endpoint")
    parser.add_argument("--direct", action="store_true", help="Directly write to DB and Qdrant instead of server API")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.video):
        print(f"Error: Video file does not exist at '{args.video}'")
        sys.exit(1)
        
    print(f"Processing video: {args.video}...")
    print(f"Extracting fingerprints starting at {args.start_time}s...")
    
    try:
        audio_fp = extract_audio_fingerprint(args.video, args.start_time)
        print(f"Successfully generated audio signature: {audio_fp}")
    except Exception as e:
        print(f"Failed to generate audio signature: {e}")
        audio_fp = f"fallback-audio-{args.title.lower().replace(' ', '-')}"
        print(f"Using fallback audio signature: {audio_fp}")
        
    try:
        visual_fp, logo_fp = extract_visual_fingerprints(args.video, args.start_time)
        print(f"Successfully generated 64-dim visual fingerprint (starts with: {visual_fp[:4]}...)")
        print(f"Successfully generated 16-dim logo fingerprint (starts with: {logo_fp[:4]}...)")
    except Exception as e:
        print(f"Error extracting visual signatures: {e}")
        print("Creating dummy signatures.")
        visual_fp = [0.5] * 64
        logo_fp = [0.5] * 16
        
    ocr_keywords = args.ocr_keywords if args.ocr_keywords else args.title.lower()
    
    payload = {
        "title": args.title,
        "category": args.category,
        "channel_name": args.channel,
        "visual_fp": visual_fp,
        "audio_fp": audio_fp,
        "logo_fp": logo_fp,
        "ocr_keywords": ocr_keywords
    }
    
    if args.direct:
        print("Ingesting directly into DB and Qdrant store...")
        try:
            from content_platform.server.db import SessionLocal
            from content_platform.server.models import ContentLibrary
            from content_platform.server.vector_store import vector_store
            import uuid
            
            external_content_id = f"{args.category}-{str(uuid.uuid4())[:8]}"
            db_item = ContentLibrary(
                external_content_id=external_content_id,
                title=args.title,
                category=args.category,
                channel_name=args.channel,
                visual_fp=json.dumps(visual_fp),
                audio_fp=audio_fp,
                logo_fp=json.dumps(logo_fp),
                ocr_keywords=ocr_keywords,
            )
            with SessionLocal() as session:
                session.add(db_item)
                session.commit()
                
            vector_store.upsert_reference(external_content_id, visual_fp, logo_fp)
            print(f"Direct Ingestion Complete! Saved as external ID: {external_content_id}")
        except Exception as e:
            print(f"Error during direct ingestion: {e}")
            sys.exit(1)
    else:
        print(f"Sending ingestion request to server API: {args.api_url}...")
        if httpx is None:
            print("Error: 'httpx' is required to make HTTP requests.")
            print("Please install it using: .venv\\Scripts\\pip install httpx")
            sys.exit(1)
            
        try:
            with httpx.Client() as client:
                response = client.post(args.api_url, json=payload, timeout=10.0)
            if response.status_code == 200:
                print("Ingestion Successful!")
                print("Response:", response.json())
            else:
                print(f"Ingestion failed with status code {response.status_code}")
                print("Response:", response.text)
        except Exception as e:
            print(f"Failed to connect to server API: {e}")
            print("Please make sure the server is running, or use the --direct flag to write directly to database.")
            sys.exit(1)


if __name__ == "__main__":
    main()
