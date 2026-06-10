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
    from content_platform.fingerprint.unified import UnifiedFingerprinter
except ImportError:
    UnifiedFingerprinter = None

try:
    from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor
except ImportError:
    DeepEmbeddingsExtractor = None

try:
    import httpx
except ImportError:
    httpx = None


def extract_audio_fingerprint(video_path: str, start_time_sec: float) -> list[float]:
    """Extracts a 10-second mono 16kHz S16_LE WAV snippet from the video,
    then computes the unified high-accuracy audio fingerprint.
    """
    if UnifiedFingerprinter is None:
        raise RuntimeError("UnifiedFingerprinter is not available in the current environment.")
    temp_wav = tempfile.mktemp(suffix=".wav")
    try:
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
        result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
        
        if not os.path.exists(temp_wav):
            raise RuntimeError(f"FFmpeg failed to produce the wav file.")
            
        with open(temp_wav, "rb") as f:
            audio_bytes = f.read()
            
        # Parse audio_bytes
        header_size = 44
        raw_data = audio_bytes[header_size:]
        num_samples = len(raw_data) // 2
        if num_samples < 100:
            raise ValueError("Extracted audio snippet is too short.")
            
        samples = struct.unpack(f"<{num_samples}h", raw_data)
        audio_ndarray = np.array(samples, dtype=np.float32) / 32768.0
        
        return UnifiedFingerprinter.audio_fingerprint(audio_ndarray, sr=16000)
    finally:
        if os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except OSError:
                pass


def extract_visual_fingerprints(video_path: str, start_time_sec: float) -> tuple[list[float], list[float]]:
    """Extracts a frame from the video at start_time_sec,
    and computes the 960-dim visual fingerprint using the DeepEmbeddingsExtractor.
    """
    if cv2 is None or DeepEmbeddingsExtractor is None:
        raise RuntimeError("OpenCV (cv2) and DeepEmbeddingsExtractor are required to extract visual fingerprints.")
        
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
    
    # 960-dim visual fingerprint
    visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
    # Dummy 16-dim logo fingerprint
    logo_fp = [0.0] * 16
    
    return visual_fp, logo_fp


def ingest_single_video(video_path: str, title: str, category: str, channel: str, ocr_keywords: str, start_time: float, api_url: str, direct: bool):
    import cv2
    import librosa
    import numpy as np
    
    print("\n" + "=" * 60)
    print(f"Ingesting: {video_path}")
    print(f"Title:     {title}")
    print(f"Category:  {category}")
    print("=" * 60)
    
    # 1. Open video
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")
        
    fps = cap.get(cv2.CAP_PROP_FPS)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if fps <= 0 or frame_count <= 0:
        raise RuntimeError(f"Invalid FPS ({fps}) or Frame Count ({frame_count})")
        
    duration = int(frame_count / fps)
    print(f"Video FPS: {fps:.2f}, Duration: {duration} seconds")
    
    # 2. Load audio track (16kHz mono)
    print("Loading audio track...")
    try:
        audio, sr = librosa.load(video_path, sr=16000, mono=True)
        print(f"Audio loaded successfully. Samples: {len(audio)}")
    except Exception as e:
        print(f"Failed to load audio: {e}. Falling back to zero-vector audio.")
        audio, sr = np.zeros(16000 * duration, dtype=np.float32), 16000

    # 3. Generate content ID (same for all segments of this video)
    import uuid
    content_id = f"{category}-{str(uuid.uuid4())[:8]}"
    
    SEGMENT_SECONDS = 10
    inserted = 0
    
    if direct:
        print("Ingesting directly into DB and Qdrant store...")
        from content_platform.server.db import SessionLocal
        from content_platform.server.models import ContentLibrary
        from content_platform.server.vector_store import vector_store
        
        session = SessionLocal()
        try:
            for sec in range(0, duration, SEGMENT_SECONDS):
                # Seek to frame at `sec`
                frame_number = int(sec * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                ret, frame = cap.read()
                if not ret:
                    continue
                    
                # Extract visual fingerprint (960-dim)
                visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
                
                # Slice audio chunk
                start_sample = sec * sr
                end_sample = min(len(audio), (sec + SEGMENT_SECONDS) * sr)
                audio_chunk = audio[start_sample:end_sample]
                if len(audio_chunk) < 100:
                    audio_chunk = np.zeros(sr * SEGMENT_SECONDS, dtype=np.float32)
                    
                # Extract audio fingerprint (130-dim)
                audio_fp = UnifiedFingerprinter.audio_fingerprint(audio_chunk, sr)
                
                logo_fp = [0.0] * 16
                final_ocr = ocr_keywords if ocr_keywords else title.lower()
                
                db_item = ContentLibrary(
                    external_content_id=content_id,
                    title=title,
                    category=category,
                    channel_name=channel,
                    segment_offset=sec,
                    visual_fp=json.dumps(visual_fp),
                    audio_fp=json.dumps(audio_fp),
                    logo_fp=json.dumps(logo_fp),
                    ocr_keywords=final_ocr,
                )
                session.add(db_item)
                
                # Sync first segment to vector store
                if sec == 0:
                    vector_store.upsert_reference(content_id, visual_fp, logo_fp)
                    
                inserted += 1
                print(f"  [{inserted}] Stored segment @ {sec}s")
                
            session.commit()
            print(f"Direct Ingestion Complete! Ingested {inserted} segments. Content ID: {content_id}")
        except Exception as e:
            session.rollback()
            print(f"Error during direct ingestion: {e}")
            raise e
        finally:
            session.close()
            cap.release()
    else:
        print(f"Sending ingestion requests to server API: {api_url}...")
        if httpx is None:
            print("Error: 'httpx' is required to make HTTP requests.")
            print("Please install it using: .venv\\Scripts\\pip install httpx")
            sys.exit(1)
            
        try:
            with httpx.Client() as client:
                for sec in range(0, duration, SEGMENT_SECONDS):
                    # Seek to frame at `sec`
                    frame_number = int(sec * fps)
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                    ret, frame = cap.read()
                    if not ret:
                        continue
                        
                    visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
                    
                    start_sample = sec * sr
                    end_sample = min(len(audio), (sec + SEGMENT_SECONDS) * sr)
                    audio_chunk = audio[start_sample:end_sample]
                    if len(audio_chunk) < 100:
                        audio_chunk = np.zeros(sr * SEGMENT_SECONDS, dtype=np.float32)
                        
                    audio_fp = UnifiedFingerprinter.audio_fingerprint(audio_chunk, sr)
                    
                    logo_fp = [0.0] * 16
                    final_ocr = ocr_keywords if ocr_keywords else title.lower()
                    
                    payload = {
                        "title": f"{title} (Segment {sec}s)",
                        "category": category,
                        "channel_name": channel,
                        "visual_fp": visual_fp,
                        "audio_fp": audio_fp,
                        "logo_fp": logo_fp,
                        "ocr_keywords": final_ocr
                    }
                    response = client.post(api_url, json=payload, timeout=15.0)
                    if response.status_code == 200:
                        inserted += 1
                        print(f"  [{inserted}] Sent segment @ {sec}s")
            print(f"Ingestion via API Complete! Ingested {inserted} segments.")
        except Exception as e:
            print(f"Failed to connect to server API: {e}")
            raise e
        finally:
            cap.release()


def main():
    parser = argparse.ArgumentParser(description="Ingest video files or folders into the content reference library.")
    parser.add_argument("--video", "-v", default="videos", help="Path to the video file or directory containing videos (default: manual_ingestion/videos)")
    parser.add_argument("--title", "-t", default=None, help="Title of the content (defaults to filename without extension)")
    parser.add_argument("--category", "-c", default="music", choices=["movie", "series", "advertisement", "music", "channel"], help="Category of the content")
    parser.add_argument("--channel", "-n", default="Local Library", help="Associated channel name")
    parser.add_argument("--ocr-keywords", "-o", default="", help="OCR keywords associated (defaults to title)")
    parser.add_argument("--start-time", "-s", type=float, default=10.0, help="Start time in seconds to extract signatures (default: 10s)")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000/api/v1/library", help="Local server API endpoint")
    parser.add_argument("--direct", action="store_true", help="Directly write to DB and Qdrant instead of server API")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.video):
        print(f"Error: Path does not exist at '{args.video}'")
        sys.exit(1)
        
    from pathlib import Path
    
    if os.path.isdir(args.video):
        video_extensions = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm")
        video_files = [f for f in Path(args.video).iterdir() if f.is_file() and f.suffix.lower() in video_extensions]
        
        if not video_files:
            print(f"No video files found in directory: {args.video}")
            sys.exit(1)
            
        print(f"Found {len(video_files)} video files in directory.")
        for idx, video_file in enumerate(video_files, 1):
            title = video_file.stem
            print(f"\nProcessing [{idx}/{len(video_files)}]: {video_file.name}")
            try:
                ingest_single_video(
                    video_path=str(video_file),
                    title=title,
                    category=args.category,
                    channel=args.channel,
                    ocr_keywords=args.ocr_keywords,
                    start_time=args.start_time,
                    api_url=args.api_url,
                    direct=args.direct
                )
            except Exception as e:
                print(f"Failed to ingest {video_file.name}: {e}")
    else:
        title = args.title if args.title else Path(args.video).stem
        try:
            ingest_single_video(
                video_path=args.video,
                title=title,
                category=args.category,
                channel=args.channel,
                ocr_keywords=args.ocr_keywords,
                start_time=args.start_time,
                api_url=args.api_url,
                direct=args.direct
            )
        except Exception as e:
            sys.exit(1)


if __name__ == "__main__":
    main()
