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
        self._audio_process = None
        self._audio_file = None
        # Simulated database cache
        self._simulated_segments = {}
        self._db_loaded = False
        self._consecutive_silence_count = 0

    def _load_simulated_segments(self) -> None:
        if self._db_loaded:
            return
        try:
            from content_platform.server.db import SessionLocal
            from content_platform.server.models import Content, ContentSegment
            import json

            with SessionLocal() as db:
                segments = (
                    db.query(ContentSegment)
                    .join(Content, Content.content_id == ContentSegment.content_id)
                    .filter(Content.title.in_(["Goyamart S01E91", "Goyamart S01E92"]))
                    .all()
                )
                for segment in segments:
                    title = segment.content.title.lower()
                    if title not in self._simulated_segments:
                        self._simulated_segments[title] = []
                    try:
                        vis = json.loads(segment.visual_fp)
                        aud = json.loads(segment.audio_fp)
                        self._simulated_segments[title].append((vis, aud))
                    except Exception:
                        pass
            logger.info("Loaded simulated segments from DB: %s", {k: len(v) for k, v in self._simulated_segments.items()})
            self._db_loaded = True
        except Exception as e:
            logger.warning("Could not load simulated segments from DB: %s. Using fallbacks.", e)

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
        
        if not hasattr(self, "_last_sim_capture_time") or self._last_sim_capture_time is None:
            self._last_sim_capture_time = now
        else:
            elapsed = (now - self._last_sim_capture_time).total_seconds()
            if elapsed < settings.capture_interval_seconds:
                return None
            self._last_sim_capture_time = now
        
        # Every 10 seconds, switch between 2 songs
        idx = (int(now.timestamp()) // 10) % 2
        song_name = "goyamart s01e91" if idx == 0 else "goyamart s01e92"

        self._load_simulated_segments()
        
        segments = self._simulated_segments.get(song_name, [])
        if segments:
            # Replicate playback behavior: cycle sequentially through reference segments
            seg_idx = (int(now.timestamp()) // 10) % len(segments)
            visual_fp, audio_fp = segments[seg_idx]
            visual_fps = [visual_fp] * 10
        else:
            # Fallback to dummy vectors of correct shapes
            if idx == 0:
                visual_fp = [0.88, 0.23, 0.61, 0.79, 0.15, 0.34, 0.92, 0.41] + [0.0] * 952
                audio_fp = [0.88, 0.23, 0.61, 0.79, 0.15, 0.34, 0.92, 0.41, 0.12, 0.44, 0.33, 0.11, 0.05] + [0.0] * 117
            else:
                visual_fp = [0.45, 0.67, 0.23, 0.89, 0.12, 0.76, 0.33, 0.54] + [0.0] * 952
                audio_fp = [0.45, 0.67, 0.23, 0.89, 0.12, 0.76, 0.33, 0.54, 0.05, 0.22, 0.11, 0.05, 0.02] + [0.0] * 117
            visual_fps = [visual_fp] * 10
        
        payload = FingerprintPayload(
            device_id=device_id,
            timestamp=now,
            visual_fps=visual_fps,
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
            
            # Start background recording on first frame of the batch
            if self._batch_start_time is None or len(self._buffer_frames) == 0:
                self._batch_start_time = datetime.now(UTC)
                if self._audio_process:
                    try:
                        self._audio_process.kill()
                        self._audio_process.wait()
                    except Exception:
                        pass
                if self._audio_file and os.path.exists(self._audio_file):
                    try:
                        os.remove(self._audio_file)
                    except OSError:
                        pass
                
                import tempfile
                import shutil
                self._audio_file = tempfile.mktemp(suffix=".wav")
                use_parecord = shutil.which("parecord") is not None
                
                audio_dev = discover_audio_device()
                # Prefer ffmpeg with detected format (pulse or alsa) for robust resampling/format conversion
                if shutil.which("ffmpeg") is not None:
                    audio_fmt = "alsa" if (audio_dev.startswith("plughw:") or audio_dev.startswith("hw:")) else "pulse"
                    cmd = [
                        "ffmpeg",
                        "-y",  # overwrite output if exists
                        "-f", audio_fmt,
                        "-i", audio_dev,
                        "-t", "10",
                        "-ac", "1",
                        "-ar", "16000",
                        "-acodec", "pcm_s16le",
                        self._audio_file,
                    ]
                    log_msg = f"Started background audio capture using ffmpeg ({audio_fmt}) from {audio_dev} to {self._audio_file}"
                elif use_parecord:
                    cmd = [
                        "parecord", "--format=s16le", "--rate=16000", "--channels=1",
                        "--file-format=wav", self._audio_file
                    ]
                    log_msg = f"Started background audio capture using parecord to {self._audio_file}"
                else:
                    cmd = [
                        "arecord", "-D", audio_dev, "-d", "10",
                        "-f", "S16_LE", "-r", "16000", "-c", "1", self._audio_file
                    ]
                    log_msg = f"Started background audio capture using {audio_dev} (arecord) to {self._audio_file}"

                try:
                    self._audio_process = subprocess.Popen(
                        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    logger.info(log_msg)
                except Exception as e:
                    logger.warning(f"Failed to start background audio capture: {e}")
                    self._audio_process = None

            # Capture frame
            frame = self._capture_frame()
            self._buffer_frames.append(frame)
            
            # Check if we have 10 seconds of data
            elapsed = (datetime.now(UTC) - self._batch_start_time).total_seconds()
            
            if elapsed >= 10 or len(self._buffer_frames) >= 10:
                # Wait for audio process to complete
                audio_bytes = b""
                if self._audio_process:
                    try:
                        # Cleanly terminate parecord if it's still running
                        if self._audio_process.poll() is None:
                            self._audio_process.terminate()
                        self._audio_process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        logger.warning("Background audio capture did not terminate. Killing process.")
                        self._audio_process.kill()
                        self._audio_process.wait()
                    except Exception as e:
                        logger.warning(f"Error waiting for audio process: {e}")
                    
                    if self._audio_file and os.path.exists(self._audio_file):
                        try:
                            with open(self._audio_file, "rb") as f:
                                audio_bytes = f.read()
                            os.remove(self._audio_file)
                        except Exception as e:
                            logger.warning(f"Failed to read batch audio file: {e}")
                    
                    self._audio_process = None
                    self._audio_file = None
                
                # Process batch
                payload = self._process_batch_pi(device_id, audio_bytes)
                
                logger.info(f"Batch processed: {len(self._buffer_frames)} frames, audio_len={len(audio_bytes)} bytes")
                
                # Reset buffer
                self._buffer_frames = []
                self._batch_start_time = None
                
                return payload
            
            return None
        
        except Exception as e:
            logger.error(f"Pi capture error: {e}")
            if self._audio_process:
                try:
                    self._audio_process.kill()
                    self._audio_process.wait()
                except Exception:
                    pass
            if self._audio_file and os.path.exists(self._audio_file):
                try:
                    os.remove(self._audio_file)
                except OSError:
                    pass
            self._audio_process = None
            self._audio_file = None
            self._buffer_frames = []
            self._batch_start_time = None
            return None

    def _process_batch_pi(self, device_id: str, audio_bytes: bytes) -> EdgeCapture:
        """Process buffered frames/audio and return single payload."""
        import cv2
        from content_platform.fingerprint.unified import UnifiedFingerprinter
        from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor
        
        logger.info(f"Processing batch: {len(self._buffer_frames)} frames")
        
        # Visual: extract fingerprint for each frame
        visual_fps = [DeepEmbeddingsExtractor.extract_visual(f) for f in self._buffer_frames]
        
        # Audio: decode WAV bytes and extract MFCC
        if audio_bytes and len(audio_bytes) >= 44:
            arr = wav_bytes_to_ndarray(audio_bytes)
            if len(arr) > 0:
                # Check for empty/silent input (e.g. all zeros or maximum amplitude close to 0)
                max_amp = float(np.max(np.abs(arr)))
                if max_amp < 0.001:
                    logger.warning("Captured audio is silent (max amplitude: %.6f). Triggering auto-healing...", max_amp)
                    self._trigger_audio_healing()
                else:
                    self._consecutive_silence_count = 0
                audio_fp = UnifiedFingerprinter.audio_fingerprint(arr, sr=16000)
            else:
                self._trigger_audio_healing()
                audio_fp = [0.0] * 130
        else:
            self._trigger_audio_healing()
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

    def _trigger_audio_healing(self) -> None:
        """
        Unmutes capture card and restarts/reconfigures PulseAudio source.
        If silent audio persists, resets the USB interface.
        """
        try:
            from pathlib import Path
            import subprocess
            
            logger.info("Starting auto-healing of audio input devices...")
            
            # Check if this is a persistent silence. We keep a count of consecutive silences.
            if not hasattr(self, "_consecutive_silence_count"):
                self._consecutive_silence_count = 0
            self._consecutive_silence_count += 1
            
            # If we have had multiple consecutive silent batches, try power-cycling the USB device
            if self._consecutive_silence_count >= 2:
                logger.warning(f"Detected {self._consecutive_silence_count} consecutive silent batches. Resetting USB capture card...")
                self._reset_usb_device()
                self._consecutive_silence_count = 0
                return
            
            # 1. Unmute ALSA capture card dynamically
            card_idx = None
            cards_path = Path("/proc/asound/cards")
            if cards_path.exists():
                for line in cards_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    if any(token in line for token in ("Video", "USB-Audio", "Macrosilicon", "C3-1 USB3")):
                        parts = line.strip().split()
                        if parts and parts[0].isdigit():
                            card_idx = parts[0]
                            break
            
            if card_idx is not None:
                logger.info(f"Unmuting ALSA mixer controls on card {card_idx}")
                subprocess.run(["amixer", "-c", card_idx, "sset", "Digital In", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["amixer", "-c", card_idx, "sset", "PCM", "on"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # Also unmute general Capture/Mic controls just in case
            subprocess.run(["amixer", "sset", "Capture", "100%", "unmute"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["amixer", "sset", "Mic", "100%", "unmute"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            # 2. Configure PipeWire / PulseAudio volume and unmute
            audio_dev = discover_audio_device()
            if audio_dev and not audio_dev.startswith("plughw:") and not audio_dev.startswith("hw:") and audio_dev != "default":
                logger.info(f"Setting volume to 100% and unmuting PipeWire/Pulse source: {audio_dev}")
                subprocess.run(["pactl", "set-source-mute", audio_dev, "0"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                subprocess.run(["pactl", "set-source-volume", audio_dev, "100%"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
                # Wake up the suspended source by reading 1 second of audio
                subprocess.run(["arecord", "-D", "pulse", "-d", "1", "-f", "S16_LE", "-r", "16000", "-c", "1", "/dev/null"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                
            logger.info("Audio auto-healing completed.")
        except Exception as e:
            logger.warning(f"Failed to perform audio auto-healing: {e}")

    def _reset_usb_device(self) -> bool:
        """
        Dynamically finds the USB port of the Macrosilicon capture card
        and power-cycles it by unbinding and rebinding the USB driver.
        """
        try:
            import os
            import subprocess
            import time
            
            # Find the port dynamically
            dev_name = None
            for dev in os.listdir("/sys/bus/usb/devices/"):
                prod_path = f"/sys/bus/usb/devices/{dev}/product"
                if os.path.exists(prod_path):
                    try:
                        with open(prod_path, "r") as f:
                            name = f.read().strip().lower()
                            if any(x in name for x in ("macrosilicon", "video", "c3-1")):
                                dev_name = dev
                                break
                    except Exception:
                        pass
            
            if not dev_name:
                logger.warning("Could not locate USB capture card in sysfs for reset.")
                return False
                
            logger.info(f"Dynamically resolved USB capture card at port {dev_name}. Power-cycling USB interface...")
            
            # Run unbind via sudo (requires sudoers NOPASSWD config)
            subprocess.run(
                f"echo '{dev_name}' | sudo tee /sys/bus/usb/drivers/usb/unbind",
                shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(2.0)
            
            # Run bind via sudo
            subprocess.run(
                f"echo '{dev_name}' | sudo tee /sys/bus/usb/drivers/usb/bind",
                shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(3.0)
            logger.info("USB interface power-cycle completed.")
            return True
        except Exception as e:
            logger.warning(f"Failed to reset USB device: {e}")
            return False


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
                timeout=duration + 5, check=True
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
