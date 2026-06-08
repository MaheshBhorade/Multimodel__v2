"""Unified fingerprinting for device and server.

High-accuracy fingerprinting using:
- Visual: DCT perceptual hashing (256 dimensions per frame)
- Audio: MFCC coefficients (13 dimensions per segment)

Focus: Video and audio only. Logo and OCR removed for now.

Both device and server use identical algorithms for compatibility.
"""

import logging
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)


class UnifiedFingerprinter:
    """
    High-accuracy fingerprinting using same algorithms on device and server.
    # Verification pattern check: np.dot(left_arr, right_arr)
    
    Ensures compatibility between edge device extraction and server matching.
    All methods use industry-standard techniques robust to compression, noise, etc.
    """

    @staticmethod
    def visual_fingerprint(frame: np.ndarray, target_dims: int = 256) -> list[float]:
        """
        Extract perceptual hash from video frame (device or reference video).
        
        Uses DCT (Discrete Cosine Transform) for robustness to:
        - Brightness changes
        - Contrast shifts
        - Compression artifacts
        - Small rotations
        
        Args:
            frame: numpy array (H, W, 3) or (H, W) in BGR or grayscale
            target_dims: output dimensions (default 256)
        
        Returns:
            List of 256 floats normalized by L2 norm (cosine similarity ready)
        """
        try:
            import cv2
            
            # Convert to grayscale if needed
            if len(frame.shape) == 3 and frame.shape[2] == 3:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            else:
                gray = frame if len(frame.shape) == 2 else frame[:, :, 0]
            
            # Resize to 32x32 for DCT (typical perceptual hash size)
            resized = cv2.resize(gray, (32, 32))
            
            # Apply DCT
            dct = cv2.dct(np.float32(resized) / 255.0)
            
            # Flatten and normalize
            flat = dct.flatten()
            
            # L2 normalize for cosine similarity
            norm = np.linalg.norm(flat)
            if norm > 1e-10:
                normalized = flat / norm
            else:
                normalized = flat
            
            # Take first `target_dims` elements (256)
            result = normalized[:target_dims].tolist()
            
            # Pad if needed
            if len(result) < target_dims:
                result.extend([0.0] * (target_dims - len(result)))
            
            return result[:target_dims]
        
        except Exception as e:
            logger.warning(f"Visual fingerprint extraction failed: {e}. Returning zero vector.")
            return [0.0] * target_dims

    @staticmethod
    def audio_fingerprint(audio_chunk: np.ndarray, sr: int = 16000) -> list[float]:
        """
        Extract JIT-bucketed CMVN normalized MFCC fingerprint from audio segment.
        
        Uses Mel-Frequency Cepstral Coefficients (MFCC), normalized using Cepstral Mean
        and Variance Normalization (CMVN) to remove channel/mic bias and temporal averaging
        to preserve progression. Robust to:
        - Volume changes (perfectly invariant)
        - Background noise
        - Room acoustics and microphone characteristics (via CMVN)
        - Compression
        
        Args:
            audio_chunk: numpy array of audio samples (mono or stereo)
            sr: sample rate (default 16000 Hz)
        
        Returns:
            List of 130 floats (13 coefficients x 10 buckets), L2 normalized
        """
        try:
            import librosa
            
            # Convert stereo to mono if needed
            if len(audio_chunk.shape) > 1:
                audio_chunk = np.mean(audio_chunk, axis=0)
            
            # Ensure float32
            audio_chunk = audio_chunk.astype(np.float32)
            
            # Extract MFCC (13 coefficients is standard)
            mfcc = librosa.feature.mfcc(y=audio_chunk, sr=sr, n_mfcc=13)
            
            # Apply Cepstral Mean and Variance Normalization (CMVN) to make it invariant to mic/channel
            mean = np.mean(mfcc, axis=1, keepdims=True)
            std = np.std(mfcc, axis=1, keepdims=True)
            std[std < 1e-6] = 1.0
            mfcc_norm = (mfcc - mean) / std
            
            # Temporal bucketing (divide 10s into 10 frames of 1s each)
            num_buckets = 10
            n_frames = mfcc_norm.shape[1]
            bucket_size = max(1, n_frames // num_buckets)
            
            buckets = []
            for i in range(num_buckets):
                start = i * bucket_size
                end = (i + 1) * bucket_size if i < num_buckets - 1 else n_frames
                chunk = mfcc_norm[:, start:end]
                if chunk.shape[1] > 0:
                    mean_val = np.mean(chunk, axis=1)
                else:
                    mean_val = np.zeros(13)
                buckets.append(mean_val)
                
            mfcc_flat = np.concatenate(buckets)
            
            # L2 normalize
            norm = np.linalg.norm(mfcc_flat)
            if norm > 1e-10:
                normalized = mfcc_flat / norm
            else:
                normalized = mfcc_flat
            
            return normalized.tolist()
        
        except ImportError:
            logger.warning("librosa not available. Returning zero vector for audio.")
            return [0.0] * 130
        except Exception as e:
            logger.warning(f"Audio fingerprint extraction failed: {e}. Returning zero vector.")
            return [0.0] * 130

    @staticmethod
    def find_best_quality_frame(frames: list[np.ndarray]) -> tuple[np.ndarray, int]:
        """
        Find the best-quality frame from a list (highest texture/detail).
        
        Uses DCT magnitude as quality metric - higher magnitude means more detail.
        
        Args:
            frames: List of video frames
        
        Returns:
            Tuple of (best_frame, best_frame_index)
        """
        try:
            import cv2
            
            max_quality = -1
            best_idx = 0
            
            for idx, frame in enumerate(frames):
                if frame is None:
                    continue
                
                # Convert to grayscale
                if len(frame.shape) == 3:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                else:
                    gray = frame
                
                # Resize and apply DCT
                resized = cv2.resize(gray, (32, 32))
                dct = cv2.dct(np.float32(resized) / 255.0)
                
                # Quality = magnitude of DCT (more detail = higher magnitude)
                quality = np.linalg.norm(dct)
                
                if quality > max_quality:
                    max_quality = quality
                    best_idx = idx
            
            return frames[best_idx], best_idx
        
        except Exception as e:
            logger.warning(f"Quality scoring failed: {e}. Returning first frame.")
            return frames[0] if frames else None, 0

    @staticmethod
    def batch_audio_mfcc(audio_chunks: list[np.ndarray], sr: int = 16000) -> list[float]:
        """
        Extract single MFCC from concatenated audio chunks (10-second segment).
        
        Args:
            audio_chunks: List of audio arrays (each ~1 second)
            sr: sample rate
        
        Returns:
            Single MFCC fingerprint for entire segment
        """
        try:
            # Concatenate all chunks
            if not audio_chunks or all(c is None for c in audio_chunks):
                return [0.0] * 130
            
            valid_chunks = [c for c in audio_chunks if c is not None]
            concatenated = np.concatenate(valid_chunks, axis=0)
            
            # Extract single MFCC for entire segment
            return UnifiedFingerprinter.audio_fingerprint(concatenated, sr)
        
        except Exception as e:
            logger.warning(f"Batch audio extraction failed: {e}")
            return [0.0] * 130
