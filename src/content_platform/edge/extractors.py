from datetime import UTC, datetime
import os
import subprocess
import struct
import logging

import numpy as np

from content_platform.edge.hardware import candidate_video_indices, discover_audio_device
from content_platform.edge.types import EdgeCapture
from content_platform.shared.config import get_settings
from content_platform.shared.models import FingerprintPayload

logger = logging.getLogger(__name__)
settings = get_settings()


def wav_bytes_to_ndarray(audio_bytes: bytes) -> np.ndarray:
    if not audio_bytes or len(audio_bytes) < 44:
        return np.array([], dtype=np.float32)
    try:
        raw_data = audio_bytes[44:]
        num_samples = len(raw_data) // 2
        samples = struct.unpack(f"<{num_samples}h", raw_data)
        return np.array(samples, dtype=np.float32) / 32768.0
    except Exception as e:
        logger.warning(f"Failed to decode wav bytes: {e}")
        return np.array([], dtype=np.float32)


class FingerprintExtractor:
    def __init__(self) -> None:
        self._capture = None
        self._capture_index: int | None = None
        # Batching support
        self._buffer_frames = []
        self._buffer_audio = []
        self._batch_start_time = None

    def capture(self, device_id: str) -> EdgeCapture | None:
        """
        Main capture method called every 1 second.
        Returns EdgeCapture only when 10 seconds of data collected (batching enabled).
        """
        if settings.edge_mode.lower() == "pi":
            return self._capture_pi_buffered(device_id)
        else:
            return self._capture_simulated_buffered(device_id)

    def _capture_simulated_buffered(self, device_id: str) -> EdgeCapture | None:
        """
        Simulated mode with 10-second batching.
        Cycles through 2 songs (not 5) for focused testing.
        """
        now = datetime.now(UTC)
        
        # Every 10 seconds, switch between 2 songs
        idx = (int(now.timestamp()) // 10) % 2
        
        # Song 1 vs Song 2
        if idx == 0:
            # Song 1 (Bangles)
            visual_fp = [0.88, 0.23, 0.61, 0.79, 0.15, 0.34, 0.92, 0.41] + [0.0] * 248  # 256 dims
            audio_fp_raw = [82, 71, 68, 65, 64, 63, 62, 61, 60, 59, 58, 57, 56]  # 13 MFCC
        else:
            # Song 2 (Shararat)
            visual_fp = [0.45, 0.67, 0.23, 0.89, 0.12, 0.76, 0.33, 0.54] + [0.0] * 248  # 256 dims
            audio_fp_raw = [75, 70, 65, 60, 55, 50, 45, 40, 35, 30, 25, 20, 15]  # 13 MFCC

        audio_fp_arr = np.array(audio_fp_raw, dtype=float)
        norm = np.linalg.norm(audio_fp_arr)
        audio_fp = (audio_fp_arr / (norm if norm > 1e-10 else 1.0)).tolist()
        
        payload = FingerprintPayload(
            device_id=device_id,
            timestamp=now,
            visual_fps=[visual_fp] * 10,
            audio_fp=audio_fp,
            snapshot_url=None,
            batch_count=10,
            batch_duration_sec=10,
            best_frame_index=0,
        )
        return EdgeCapture(payload=payload)

    def _capture_pi_buffered(self, device_id: str) -> EdgeCapture | None:
        """
        Pi mode with 10-second buffering.
        Collects frames for 10 seconds, then batch processes all.
        """
        try:
            import cv2
            from content_platform.fingerprint.unified import UnifiedFingerprinter
            
            # Capture frame
            frame = self._capture_frame()
            self._buffer_frames.append(frame)
            
            # Record short audio snippet (1 sec)
            try:
                audio_dev = discover_audio_device()
                audio_bytes = self._record_audio_snippet(audio_dev, duration=1)
                if audio_bytes:
                    self._buffer_audio.append(audio_bytes)
            except Exception as e:
                logger.warning(f"Audio recording failed: {e}")
            
            # Initialize batch timer on first capture
            if self._batch_start_time is None:
                self._batch_start_time = datetime.now(UTC)
            
            # Check if we have 10 seconds of data
            elapsed = (datetime.now(UTC) - self._batch_start_time).total_seconds()
            
            if elapsed >= 10 or len(self._buffer_frames) >= 10:
                # Process batch
                payload = self._process_batch_pi(device_id)
                
                # Reset buffer
                self._buffer_frames = []
                self._buffer_audio = []
                self._batch_start_time = None
                
                logger.info(f"Batch processed: {len(self._buffer_frames)} frames, {len(self._buffer_audio)} audio snippets")
                
                return payload
            
            return None
        
        except Exception as e:
            logger.error(f"Pi capture error: {e}")
            self._buffer_frames = []
            self._buffer_audio = []
            self._batch_start_time = None
            return None

    def _process_batch_pi(self, device_id: str) -> EdgeCapture:
        """Process buffered frames/audio and return single payload."""
        import cv2
        from content_platform.fingerprint.unified import UnifiedFingerprinter
        
        logger.info(f"Processing batch: {len(self._buffer_frames)} frames")
        
        # Visual: extract fingerprint for each frame
        visual_fps = [UnifiedFingerprinter.visual_fingerprint(f) for f in self._buffer_frames]
        
        # Audio: decode all WAV bytes chunks and extract MFCC
        audio_arrays = [wav_bytes_to_ndarray(b) for b in self._buffer_audio]
        audio_arrays = [arr for arr in audio_arrays if len(arr) > 0]
        if audio_arrays:
            concatenated = np.concatenate(audio_arrays)
            audio_fp = UnifiedFingerprinter.audio_fingerprint(concatenated, sr=16000)
        else:
            audio_fp = [0.0] * 130
            
        # Snapshot: find best quality frame
        best_frame, best_idx = UnifiedFingerprinter.find_best_quality_frame(self._buffer_frames)
        ok, buffer = cv2.imencode(".jpg", best_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
        
        timestamp = datetime.now(UTC)
        
        payload = FingerprintPayload(
            device_id=device_id,
            timestamp=timestamp,
            visual_fps=visual_fps,
            audio_fp=audio_fp,
            snapshot_url=None,
            batch_count=len(self._buffer_frames),
            batch_duration_sec=10,
            best_frame_index=best_idx,
        )
        
        return EdgeCapture(
            payload=payload,
            snapshot_bytes=buffer.tobytes() if ok else None,
            snapshot_filename=f"{device_id}_{int(timestamp.timestamp())}.jpg",
        )

    def _record_audio_snippet(self, device: str, duration: int = 10) -> bytes:
        import tempfile
        temp_wav = tempfile.mktemp(suffix=".wav")
        try:
            cmd = [
                "arecord", "-D", device, "-d", str(duration),
                "-f", "S16_LE", "-r", "16000", "-c", "1", temp_wav
            ]
            subprocess.run(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=duration + 2, check=True
            )
            if os.path.exists(temp_wav):
                with open(temp_wav, "rb") as f:
                    data = f.read()
                os.remove(temp_wav)
                return data
        except Exception as e:
            logger.warning(
                "Failed to record real audio using %s: %s. Using simulated audio signature.",
                device, e
            )
        finally:
            if os.path.exists(temp_wav):
                try:
                    os.remove(temp_wav)
                except OSError:
                    pass
        return b""

    def _generate_audio_fingerprint(self, audio_bytes: bytes) -> str:
        if not audio_bytes or len(audio_bytes) < 44:
            # Fallback to simulated audio fingerprinting based on time
            import time
            t = int(time.time())
            if t % 2 == 0:
                return "dilwale-main-track"
            return "sony-max-theme"

        try:
            header_size = 44
            raw_data = audio_bytes[header_size:]
            num_samples = len(raw_data) // 2
            if num_samples < 100:
                return "fallback-audio-signature"

            # Unpack S16 PCM samples
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
                normalized_energies = [int((e / max_energy) * 100) for e in energies]
            else:
                normalized_energies = [0] * len(energies)

            return "ae-hash-" + "-".join(str(e) for e in normalized_energies)
        except Exception as e:
            logger.warning("Error generating audio fingerprint: %s", e)
            return "fallback-audio-signature"

    def _capture_frame(self) -> np.ndarray:
        try:
            import cv2
        except ImportError as exc:  # pragma: no cover - only exercised on Pi runtime
            raise RuntimeError("OpenCV is required for CRP_EDGE_MODE=pi") from exc

        cap = self._ensure_capture(cv2)
        ok, frame = cap.read()
        if not ok:
            self._reset_capture()
            cap = self._ensure_capture(cv2)
            ok, frame = cap.read()
        if not ok:
            raise RuntimeError(f"Failed to capture frame from index {self._capture_index}")
        return frame

    def _ensure_capture(self, cv2_module):
        if self._capture is not None and self._capture.isOpened():
            return self._capture

        for idx in candidate_video_indices():
            cap = cv2_module.VideoCapture(idx)
            cap.set(cv2_module.CAP_PROP_FOURCC, cv2_module.VideoWriter_fourcc(*"YUYV"))
            cap.set(cv2_module.CAP_PROP_FRAME_WIDTH, 1280)
            cap.set(cv2_module.CAP_PROP_FRAME_HEIGHT, 720)
            ok, _ = cap.read()
            if ok:
                self._capture = cap
                self._capture_index = idx
                return cap
            cap.release()

        raise RuntimeError("No active HDMI capture device found on the Pi")

    def _reset_capture(self) -> None:
        if self._capture is not None:
            self._capture.release()
        self._capture = None
        self._capture_index = None

    def _frame_to_embedding(self, frame: np.ndarray) -> list[float]:
        import cv2
        if len(frame.shape) == 3 and frame.shape[2] == 3:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        else:
            gray = frame
        resized = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
        normalized = resized / 255.0
        return [round(float(val), 4) for val in normalized.flatten().tolist()]

    def _run_ocr(self, frame: np.ndarray) -> str:
        try:
            import cv2
            import pytesseract
        except ImportError:
            brightness = int(frame.mean())
            return f"pi-capture brightness-{brightness}"

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        text = pytesseract.image_to_string(thresh, config="--psm 6").strip()
        if text:
            return " ".join(text.split())[:500]

        brightness = int(frame.mean())
        return f"pi-capture brightness-{brightness}"
