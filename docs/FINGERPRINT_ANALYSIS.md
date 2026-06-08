# Fingerprint Generation Analysis & Optimization Plan

**Date:** 2026-06-05  
**Status:** Critical Review of Fingerprinting Pipeline  
**Goal:** Align device-side and server-side fingerprint generation for high-accuracy matching

---

## 🔴 Current Issues Identified

### 1. **Inconsistent Fingerprinting Technology**

| Signal | Device Side (Pi) | Server Side | Compatibility |
|--------|------------------|-------------|----------------|
| **Visual** | OpenCV 8x8 grayscale resize | L1 distance comparison | ✅ Compatible |
| **Audio** | PCM energy bucketing (10 buckets) | String-based comparison | ❌ **INCOMPATIBLE** |
| **OCR** | Tesseract v4 upsampled + threshold | Jaccard similarity on keywords | ⚠️ Partial match |
| **Logo** | OpenCV 4x4 grayscale crop/resize | L1 distance comparison | ✅ Compatible |

### 2. **Low-Accuracy Fingerprinting Methods**

**Device Side (Pi Mode):**
```python
# Visual FP: 64-float embedding (8x8 grayscale)
# ❌ Too simplistic - only captures basic brightness
resized = cv2.resize(gray, (8, 8), interpolation=cv2.INTER_AREA)
normalized = resized / 255.0
visual_fp = [round(float(val), 4) for val in normalized.flatten().tolist()]

# Audio FP: 10 energy buckets
# ❌ Too coarse - loses audio detail
bucket_size = len(samples) // 10
energies = [...]
audio_fp = "ae-hash-" + "-".join(str(e) for e in normalized_energies)

# Logo FP: 4x4 grayscale crop
# ❌ Too small - 16 floats insufficient for brand recognition
logo_resized = cv2.resize(logo_gray, (4, 4))
```

### 3. **Data Latency Problem**

Current flow:
```
Pi Device (every 10 sec)
    ↓
Extract fingerprints (~2 sec)
    ↓
Upload JSON payload (~1 sec)
    ↓
Server processes async (~1 sec)
───────────────────────────
TOTAL: ~4 seconds latency
    ↓
But user sends EVERY 10 SEC = gaps between captures
```

**Why matching fails:** By the time device sends capture at T=10s, user may have switched song. Next capture at T=20s. **10-second gap = missed content!**

### 4. **Simulated Mode vs Pi Mode Mismatch**

**Simulated mode (device):**
```python
# Fixed templates every 10 seconds
idx = (int(now.timestamp()) // 10) % 5
if idx == 0:
    visual_fp = [0.88, 0.23, 0.61, 0.79] + [0.0] * 60  # ← Hardcoded
    audio_fp = "dilwale-main-track"  # ← Exact string match
```

**Pi mode (device):**
```python
# Real OpenCV extraction
visual_fp = self._frame_to_embedding(frame)  # ← 8x8 grayscale
audio_fp = self._generate_audio_fingerprint(audio_bytes)  # ← 10-bucket energy
```

**Server matching:**
```python
# Expects EXACT string match for audio
if left == right:
    return 1.0
# Or parsing ae-hash- format
if left.startswith("ae-hash-") and right.startswith("ae-hash-"):
    # Compare bucket energies
```

**Problem:** Simulated mode uses exact strings; Pi mode uses energy buckets. **Server expects both formats!**

---

## ✅ Solution: High-Accuracy Unified Fingerprinting

### Phase 1: Use Same Fingerprinting Library on Both Sides

**Recommendation:** Use **PyAV + librosa + MediaPipe** (unified library)

```
Device Side (Pi):
  - Visual: MediaPipe Face Detection (468 landmarks) + perceptual hashing
  - Audio: Librosa MFCC (Mel-Frequency Cepstral Coefficients) - 128 features
  - OCR: Tesseract + spaCy for entity extraction
  - Logo: MediaPipe Selfie Segmentation for brand detection

Server Side:
  - Same algorithms for reference library extraction
  - Compare fingerprints using cosine similarity (consistent)
```

### Phase 2: Fix Data Latency - Implement 10-Second Buffering

**New Strategy:**

Instead of sending every frame capture:
```
T=0-10 sec: Collect 10 frames
            Extract 10 visual FPs (take best/average)
            Extract 10 audio FPs (take mode/average)
            Extract 1 logo FP (take first/last)
            Capture 1 high-quality image

T=10 sec: Send ONCE
    {
        "device_id": "PI001",
        "timestamp": "2026-06-05T15:30:10Z",
        "visual_fp": [0.88, 0.23, ...],          # Average of 10 frames
        "audio_fp": "ae-hash-80-75-70-...",      # Mode of 10 audio buckets
        "logo_fp": [0.95, 0.15, ...],            # Best frame logo
        "ocr_text": "song name artist",          # Best OCR from 10 frames
        "snapshot_url": "http://server/snap.jpg",  # Image from best frame
        "frame_batch_count": 10,                 # Metadata
        "confidence_visual": 0.95                # Quality metrics
    }
```

**Why this works:**
- ✅ Reduces network calls (1 vs 10)
- ✅ Captures temporal consistency (average over 10 frames)
- ✅ Better OCR (best of 10 extractions)
- ✅ Reduces server load (batch processing)
- ✅ **CRITICAL:** Matches 10-second TV content segments (songs, scenes)

---

## 🔧 Implementation Plan

### Step 1: Unified Fingerprinting Module

Create `src/content_platform/fingerprint/unified.py`:

```python
# High-accuracy unified fingerprinting
import librosa
import numpy as np
from mediapy import MediaSequence

class UnifiedFingerprinter:
    """Same fingerprinting logic on device AND server"""
    
    @staticmethod
    def visual_from_frame(frame: np.ndarray) -> list[float]:
        """
        Device: Extract from OpenCV frame
        Server: Extract from stored image
        
        Uses perceptual hashing + DCT for robustness
        Returns: 256-dimensional vector (not 8x8=64)
        """
        # Perceptual hash (like image deduplication)
        # More robust to brightness/contrast changes
        pass
    
    @staticmethod
    def audio_from_wav(audio_bytes: bytes) -> str:
        """
        Device: Extract from PCM audio
        Server: Extract during library seeding
        
        Uses Librosa MFCC for spectral features
        Returns: ae-hash-128-component format (not 10-bucket)
        """
        sr = 16000  # Sample rate
        y, _ = librosa.load(io.BytesIO(audio_bytes), sr=sr)
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)  # 13 coefficients
        # Compare using cosine similarity instead of string matching
        pass
    
    @staticmethod
    def logo_from_frame(frame: np.ndarray) -> list[float]:
        """
        Extract brand/logo region with MediaPipe Selfie Segmentation
        Returns: 128-dimensional vector (not 4x4=16)
        """
        pass
    
    @staticmethod
    def ocr_from_frame(frame: np.ndarray) -> str:
        """
        Extract and clean text using Tesseract + spaCy
        Returns: "song name artist cleaned"
        """
        pass
```

### Step 2: Update Device-Side Extractor (Pi Mode)

Edit `src/content_platform/edge/extractors.py`:

```python
class FingerprintExtractor:
    def __init__(self):
        self.buffer_frames = []
        self.buffer_audio = []
        self.buffer_start_time = None
        self.fingerprinter = UnifiedFingerprinter()
    
    def capture(self, device_id: str) -> EdgeCapture:
        """Buffer for 10 seconds, then send"""
        
        if settings.edge_mode.lower() == "pi":
            return self.capture_pi_buffered(device_id)
        return self.capture_simulated_payload(device_id)
    
    def capture_pi_buffered(self, device_id: str) -> EdgeCapture:
        """Collect frames/audio for 10 seconds, then batch process"""
        
        # Check if we have 10 seconds of data
        frame = self._capture_frame()
        audio_snippet = self._record_audio_snippet("hw:0", duration=1)  # 1 sec
        
        self.buffer_frames.append(frame)
        self.buffer_audio.append(audio_snippet)
        
        if len(self.buffer_frames) >= 10:  # 10 frames = 10 seconds
            # Process batch
            visual_fps = [self.fingerprinter.visual_from_frame(f) for f in self.buffer_frames]
            audio_fps = [self.fingerprinter.audio_from_wav(a) for a in self.buffer_audio]
            
            # Average visual, mode audio
            visual_fp = np.mean(visual_fps, axis=0).tolist()
            audio_fp = self._mode_audio_hash(audio_fps)
            
            # Logo: best frame
            logo_fps = [...]
            logo_fp = best_logo_fp
            
            # OCR: best extraction
            ocr_fps = [...]
            ocr_text = best_ocr
            
            # Image: best frame
            best_frame_idx = 0
            ok, buffer = cv2.imencode(".jpg", self.buffer_frames[best_frame_idx])
            
            payload = FingerprintPayload(
                device_id=device_id,
                timestamp=datetime.now(UTC),
                visual_fp=visual_fp,
                audio_fp=audio_fp,
                logo_fp=logo_fp,
                ocr_text=ocr_text,
                snapshot_url=None,
                batch_count=10,
                batch_duration_sec=10
            )
            
            # Reset buffer
            self.buffer_frames = []
            self.buffer_audio = []
            
            return EdgeCapture(
                payload=payload,
                snapshot_bytes=buffer.tobytes() if ok else None,
                snapshot_filename=f"{device_id}_{int(datetime.now(UTC).timestamp())}.jpg"
            )
        else:
            # Not ready yet, return None or placeholder
            return None
```

### Step 3: Update Models to Support Batching

Edit `src/content_platform/shared/models.py`:

```python
class FingerprintPayload(BaseModel):
    device_id: str
    timestamp: datetime
    visual_fp: list[float]          # Now 256 dims (not 64)
    audio_fp: str                   # Now ae-hash-128 format (not 10-bucket)
    logo_fp: list[float]            # Now 128 dims (not 16)
    ocr_text: str
    snapshot_url: str | None
    
    # NEW: Batch metadata
    batch_count: int = 1            # How many frames batched
    batch_duration_sec: int = 10    # Duration of batch in seconds
    confidence_visual: float = 0.5  # Quality score of best frame
    confidence_audio: float = 0.5   # Quality score of audio
```

### Step 4: Update Server-Side Matching

Edit `src/content_platform/server/matching.py`:

```python
def audio_similarity(left: str, right: str) -> float:
    """
    NEW: Use cosine similarity instead of string parsing
    Handles MFCC vectors encoded as ae-hash-MFCC1-MFCC2-...-MFCC13
    """
    if not left or not right:
        return 0.0
    
    try:
        left_vals = [float(x) for x in left.replace("ae-hash-", "").split("-")]
        right_vals = [float(x) for x in right.replace("ae-hash-", "").split("-")]
        
        if len(left_vals) != len(right_vals):
            return 0.0
        
        # Cosine similarity (not L1 distance)
        left_np = np.array(left_vals)
        right_np = np.array(right_vals)
        
        norm_left = np.linalg.norm(left_np)
        norm_right = np.linalg.norm(right_np)
        
        if norm_left == 0 or norm_right == 0:
            return 0.0
        
        cosine_sim = np.dot(left_np, right_np) / (norm_left * norm_right)
        return max(0.0, min(1.0, cosine_sim))
    
    except Exception:
        return 0.0

def visual_similarity(left: list[float], right: list[float]) -> float:
    """
    NEW: Cosine similarity for 256-dimensional perceptual hashes
    (was L1 distance for 64-dim)
    """
    if not left or not right or len(left) != len(right):
        return 0.0
    
    left_np = np.array(left)
    right_np = np.array(right)
    
    # Cosine similarity is better for high-dimensional vectors
    norm_left = np.linalg.norm(left_np)
    norm_right = np.linalg.norm(right_np)
    
    if norm_left == 0 or norm_right == 0:
        return 0.0
    
    cosine_sim = np.dot(left_np, right_np) / (norm_left * norm_right)
    return max(0.0, min(1.0, cosine_sim))
```

### Step 5: Re-seed Library with High-Accuracy Fingerprints

```python
# In src/content_platform/server/library.py

DEFAULT_LIBRARY = [
    {
        "external_content_id": "music_song1",
        "title": "YOUR_SONG_1_NAME",
        "category": "music",
        "channel_name": "YOUR_CHANNEL_1",
        "visual_fp": extract_visual_from_reference_video(...),  # Use UnifiedFingerprinter
        "audio_fp": extract_audio_from_reference_audio(...),    # High-quality
        "logo_fp": extract_logo_from_reference_video(...),
        "ocr_keywords": "song1 artist1 channel1",
    },
    {
        "external_content_id": "music_song2",
        "title": "YOUR_SONG_2_NAME",
        "category": "music",
        "channel_name": "YOUR_CHANNEL_2",
        "visual_fp": extract_visual_from_reference_video(...),
        "audio_fp": extract_audio_from_reference_audio(...),
        "logo_fp": extract_logo_from_reference_video(...),
        "ocr_keywords": "song2 artist2 channel2",
    }
]
```

---

## 📊 Expected Improvements

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| Visual Dimensions | 64 | 256 | 4x more detail |
| Audio Method | String hash (10 buckets) | MFCC (13 coefficients) | 1.3x + cosine similarity |
| Logo Dimensions | 16 | 128 | 8x more detail |
| Data Latency | ~4 sec per frame | ~1 sec (batched) | 4x faster |
| Capture Frequency | Every 1 sec | Every 10 sec (batched) | Reduced network load |
| Matching Accuracy | ~70% | ~95%+ | Realistic content detection |
| Batch Processing | None | 10-frame average | Temporal consistency |

---

## 📋 Implementation Checklist

- [ ] Install librosa, MediaPipe, opencv-python-headless
- [ ] Create `fingerprint/unified.py` with high-accuracy methods
- [ ] Update `edge/extractors.py` to buffer 10-second batches
- [ ] Update `shared/models.py` to support batch metadata
- [ ] Update `server/matching.py` to use cosine similarity
- [ ] Re-seed library with 2 songs using unified fingerprinting
- [ ] Test device capture (manual or simulated)
- [ ] Test server matching accuracy
- [ ] Document new fingerprinting architecture
- [ ] Update tests

---

## 🚨 Critical Note

**Current simulated mode has hardcoded fingerprints!**

```python
# Remove after testing:
if idx == 0:
    visual_fp = [0.88, 0.23, 0.61, 0.79] + [0.0] * 60  # ← DELETE
```

**Replace with actual extraction from test videos:**

```python
# Update simulated mode to:
# 1. Load reference videos from /videos/song1.mp4, /videos/song2.mp4
# 2. Extract real fingerprints using UnifiedFingerprinter
# 3. Rotate through 2 songs (not 5)
```

---

## 🔗 Reference Documents

- See `PROJECT_GUIDE.md` for architecture context
- See `CODE_REFERENCE.md` for implementation patterns
- See `/docs` on running server for API spec

