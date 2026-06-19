# CRP Improvement Suggestions: Real-time Speed & Maximum Accuracy

This document compiles the proposed suggestions, architectural changes, and optimizations to achieve maximum content recognition accuracy at real-time speeds.

---

## 1. Core Matching Engine & Accuracy Optimizations

### 1.1 Temporal Sequence Matching (Viterbi Decoding / HMM)
* **Problem**: Single-frame or single-second votes are independent. Isolated noise or brief similar frames (e.g., black frames, transitions, shared intros) can cause sporadic false matching.
* **Suggestion**: Replace simple voting with a temporal sequence decoder (Hidden Markov Model / Viterbi decoding). Ensure that matching segments must follow chronological order (e.g., segment $N \to N+1$). This dramatically cuts down false positives and handles logo/ad breaks gracefully.

### 1.2 Fine-Tuning Decision Thresholds & Multi-Modal Weights
* **Problem**: Fixed visual (70%) and audio (30%) weighting might not perform optimally across different programming types (e.g., high-action sports vs. talk shows).
* **Suggestion**: Implement dynamic, confidence-weighted fusion. If audio has high contrast (clear speech/music matched with high similarity), increase the audio weight. If visual is highly static, rely more on audio or OCR.

### 1.3 Mitigation for Flickering/Fluctuating Matches (Episode Switching)
* **Problem**: When audio is missing (`A: 0.00`), matching relies entirely on visual vectors. When multiple reference episodes share visually similar intros, colors, or static scenes (yielding visual scores between `0.75` and `0.85`), the top match fluctuates wildly between different shows/episodes frame-by-frame (e.g., flickering between `Goyamart S01E51` and `MI GEXECIK OR S01E07`).
* **Suggestion**:
  - **Hysteresis & Temporal Smoothing**: Maintain a rolling window of recent matches. Do not trigger a session/show transition unless a different show is matched consistently for at least 3-5 seconds.
  - **Sequential Offset Checking**: Heavily boost the match score of segments that chronologically follow the previously matched segment ($t_{now} \approx t_{prev} + 1$).
  - **Strict Thresholds for Audio-Less Matches**: If audio is unavailable, raise the visual match threshold significantly (e.g., to `0.90`) to prevent low-confidence visual matches from causing incorrect switches.

---

## 2. Real-Time Performance & Vector Store Scaling

### 2.1 Transition from FAISS memory-mapped files to Qdrant
* **Problem**: The current SQLite and flat FAISS index setups are run in-process. While fast for small reference libraries, they don't scale to 10,000+ hours of video without severe memory footprint and lock contention.
* **Suggestion**: Use Qdrant with HNSW indexes:
  - Supports high-throughput, sub-millisecond vector similarity search.
  - Native payload filtering (e.g., search only visual embeddings belonging to a specific channel/subset to reduce search space).

### 2.2 Offload Matching to Background Workers (Celery / Dramatiq)
* **Problem**: Ingesting and matching payloads inside FastAPI threadpools / background tasks blocks the main event loop under heavy load.
* **Suggestion**: Decouple ingestion from matching.
  - FastAPI `/api/v1/captures` only receives the payload, writes it to a fast queue (e.g., Redis / RabbitMQ), and immediately returns HTTP 202.
  - Celery / Dramatiq worker processes pull from the queue, execute the vector matching, and update results asynchronously.

---

## 3. Edge Agent (Raspberry Pi) Optimizations

### 3.1 Model Quantization & Hardware Acceleration (ONNX Runtime)
* **Problem**: Running heavy deep-learning visual feature extractors on the Pi CPU can bottleneck the 10-second capture loop.
* **Suggestion**: Convert visual/audio embedding models to ONNX format and apply INT8 quantization. Utilize the Pi's GPU/VPU if available, or lightweight models (e.g., MobileNetV3-based embeddings).

### 3.2 Permanent Audio Capture Solution
* **Problem**: Edge audio input configuration can be unstable and drop out.
* **Suggestion**: 
  - Lock hardware audio card index using ALSA configurations (`/etc/modprobe.d/alsa-base.conf`).
  - Add self-healing/retry mechanisms in the edge agent that restart the audio stream or reload the ALSA module if continuous zero-byte arrays (silent inputs) are detected.
