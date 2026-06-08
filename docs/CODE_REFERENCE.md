# Code Reference - Important Code Snippets & Patterns

## 1. FINGERPRINT PAYLOAD SCHEMA

**File:** `src/content_platform/shared/models.py`

```python
class FingerprintPayload(BaseModel):
    """Data sent from edge device to server"""
    device_id: str                          # Unique device identifier
    timestamp: datetime                     # UTC capture time
    visual_fp: list[float]                  # 64-dimensional visual features
    audio_fp: str                           # Audio fingerprint (string hash or "ae-hash-...")
    logo_fp: list[float]                    # 16-dimensional logo features
    ocr_text: str                           # Raw text detected on screen
    snapshot_url: str                       # HTTP URL to frame image

class RecognitionResult(BaseModel):
    """Server's match result"""
    content_id: str | None = None
    content_name: str                       # "Dilwale", "Unknown Content", etc.
    content_type: str                       # "movie", "series", "channel", "advertisement", "unknown"
    confidence: float                       # 0.0-1.0 confidence score
    breakdown: MatchBreakdown                # Individual signal scores
    matched_channel: str | None             # e.g., "Sony Max"

class MatchBreakdown(BaseModel):
    """Individual signal contributions"""
    visual_score: float                     # 0.0-1.0
    audio_score: float                      # 0.0-1.0
    ocr_score: float                        # 0.0-1.0
    logo_score: float                       # 0.0-1.0
```

## 2. BACKGROUND MATCHING TASK

**File:** `src/content_platform/server/main.py`

```python
def process_matching_background(capture_id: int, payload: FingerprintPayload) -> None:
    """
    Runs asynchronously after capture is stored.
    Updates capture status and creates recognition result record.
    """
    with SessionLocal() as db:
        try:
            # Get capture from DB
            capture = db.query(Capture).filter(Capture.id == capture_id).first()
            if not capture:
                return
            
            # Run matching algorithm
            result = match_content(db, payload)
            
            # Create result record
            record = RecognitionResultRecord(
                capture_id=capture.id,
                content_name=result.content_name,
                content_type=result.content_type,
                confidence=result.confidence,
                visual_score=result.breakdown.visual_score,
                audio_score=result.breakdown.audio_score,
                ocr_score=result.breakdown.ocr_score,
                logo_score=result.breakdown.logo_score,
            )
            db.add(record)
            
            # Update capture status
            capture.status = "matched"
            db.commit()
            
            logger.info(f"Matched capture {capture_id}: {result.content_name}")
        except Exception as e:
            logger.error(f"Failed to match capture {capture_id}: {e}")
            capture.status = "failed"
            db.commit()
```

## 3. VECTOR STORE ADAPTER (Qdrant)

**File:** `src/content_platform/server/vector_store.py`

```python
class VectorStore:
    def __init__(self, url: str = "http://localhost:6333"):
        try:
            self.client = qdrant_client.QdrantClient(url=url)
            self.client.get_collections()  # Test connection
            self.ready = True
        except Exception as e:
            logger.warning(f"Qdrant unavailable: {e}. Using in-memory fallback.")
            self.ready = False
            self.memory_store = {}  # Dict[str, {"visual": [...], "logo": [...]}]
    
    def search_visual(self, vector: list[float], limit: int = 10) -> list[dict]:
        """Search for visually similar content"""
        if not self.ready:
            return self._memory_search_visual(vector, limit)
        
        # Real Qdrant search
        results = self.client.search(
            collection_name="visual_embeddings",
            query_vector=vector,
            limit=limit,
            score_threshold=0.7
        )
        return [{"content_id": r.payload["id"], "score": r.score} for r in results]
    
    def _memory_search_visual(self, vector: list[float], limit: int) -> list[dict]:
        """Fallback: in-memory L1 distance search"""
        scores = []
        for content_id, embeddings in self.memory_store.items():
            dist = sum(abs(a - b) for a, b in zip(vector, embeddings["visual"])) / len(vector)
            sim = max(0.0, 1.0 - dist)
            if sim >= 0.7:
                scores.append({"content_id": content_id, "score": sim})
        return sorted(scores, key=lambda x: x["score"], reverse=True)[:limit]
    
    def upsert_reference(self, content_id: str, visual: list[float], logo: list[float]):
        """Add/update content in vector store"""
        self.memory_store[content_id] = {"visual": visual, "logo": logo}
        
        if self.ready:
            # Also upsert to Qdrant if available
            # Implementation depends on Qdrant collection setup
            pass

vector_store = VectorStore(url=get_settings().qdrant_url)
```

## 4. CAPTURE QUERY WITH PAGINATION & FILTERING

**File:** `src/content_platform/server/main.py`

```python
@app.get("/api/v1/captures")
def list_captures(
    device_id: str | None = None,
    only_unknown: bool = False,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """
    Stream captures with optional filtering.
    
    Query parameters:
    - device_id: Filter by device
    - only_unknown: Only show unmatched content
    - limit: Page size (max 100)
    - offset: Pagination offset
    """
    query = db.query(Capture).outerjoin(RecognitionResultRecord)
    
    if device_id:
        query = query.join(Device).filter(Device.device_id == device_id)
    
    if only_unknown:
        query = query.filter(RecognitionResultRecord.content_type == "unknown")
    
    total = query.count()
    captures = query.order_by(Capture.captured_at.desc()).limit(limit).offset(offset).all()
    
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [
            {
                "id": c.id,
                "device_id": c.device.device_id if c.device else "Unknown",
                "captured_at": c.captured_at.isoformat(),
                "snapshot_url": c.snapshot_url,
                "status": c.status,
                "result": {
                    "content_name": c.result.content_name,
                    "content_type": c.result.content_type,
                    "confidence": c.result.confidence,
                    "breakdown": {
                        "visual_score": c.result.visual_score,
                        "audio_score": c.result.audio_score,
                        "ocr_score": c.result.ocr_score,
                        "logo_score": c.result.logo_score,
                    }
                } if c.result else None
            }
            for c in captures
        ]
    }
```

## 5. MANUAL CAPTURE RESOLUTION

**File:** `src/content_platform/server/main.py`

```python
@app.post("/api/v1/captures/{capture_id}/resolve")
def resolve_capture(
    capture_id: int,
    payload: CaptureResolvePayload,  # {title, category, channel_name}
    db: Session = Depends(get_db)
):
    """
    User manually identifies unknown content.
    Creates new library entry and updates capture result.
    """
    capture = db.query(Capture).filter(Capture.id == capture_id).first()
    if not capture:
        raise HTTPException(status_code=404, detail="Capture not found")
    
    # Generate unique ID
    external_content_id = f"manual-{payload.category}-{uuid.uuid4().hex[:8]}"
    
    # Create library entry with capture's fingerprints
    library_item = ContentLibrary(
        external_content_id=external_content_id,
        title=payload.title,
        category=payload.category,
        channel_name=payload.channel_name,
        visual_fp=capture.visual_fp,  # Reuse fingerprints
        audio_fp=capture.audio_fp,
        logo_fp=capture.logo_fp,
        ocr_keywords=capture.ocr_text,
    )
    db.add(library_item)
    db.flush()
    
    # Sync to vector store
    vector_store.upsert_reference(
        external_content_id,
        json.loads(capture.visual_fp),
        json.loads(capture.logo_fp),
    )
    
    # Update recognition result
    if capture.result:
        capture.result.content_name = payload.title
        capture.result.content_type = payload.category
        capture.result.confidence = 1.0
    else:
        result_record = RecognitionResultRecord(
            capture_id=capture.id,
            content_name=payload.title,
            content_type=payload.category,
            confidence=1.0,
            visual_score=1.0,
            audio_score=1.0,
            ocr_score=1.0,
            logo_score=1.0
        )
        db.add(result_record)
    
    db.commit()
    return {"status": "resolved", "library_id": library_item.id}
```

## 6. ANALYTICS ENDPOINTS

**File:** `src/content_platform/server/main.py`

```python
@app.get("/api/v1/analytics/overview")
def get_analytics_overview(db: Session = Depends(get_db)):
    """Dashboard KPIs"""
    total_captures = db.query(Capture).count()
    active_devices = db.query(Device).filter(Device.status == "active").count()
    pending_reviews = (
        db.query(Capture)
        .join(RecognitionResultRecord)
        .filter(RecognitionResultRecord.content_type == "unknown")
        .count()
    )
    return {
        "total_captures": total_captures,
        "active_devices": active_devices,
        "pending_reviews": pending_reviews,
    }

@app.get("/api/v1/analytics/share")
def get_analytics_share(db: Session = Depends(get_db)):
    """Content type breakdown"""
    results = db.query(
        RecognitionResultRecord.content_type,
        func.count(RecognitionResultRecord.id).label("count")
    ).group_by(RecognitionResultRecord.content_type).all()
    return {r.content_type: r.count for r in results}

@app.get("/api/v1/analytics/timeline")
def get_analytics_timeline(db: Session = Depends(get_db)):
    """Last 20 recognized captures in timeline"""
    subquery = (
        db.query(Capture.id)
        .join(RecognitionResultRecord)
        .order_by(Capture.captured_at.desc())
        .limit(20)
        .subquery()
    )
    results = (
        db.query(Capture)
        .join(RecognitionResultRecord)
        .filter(Capture.id.in_(select(subquery.c.id)))
        .order_by(Capture.captured_at.asc())
        .all()
    )
    return [
        {
            "timestamp": c.captured_at.isoformat(),
            "content_name": c.result.content_name,
            "category": c.result.content_type,
            "device_id": c.device.device_id if c.device else "Unknown",
        }
        for c in results if c.result
    ]
```

## 7. EDGE AGENT CAPTURE LOOP

**File:** `src/content_platform/edge/agent.py`

```python
class EdgeAgent:
    async def run_forever(self):
        """Main loop: capture → fingerprint → upload"""
        while True:
            try:
                # Extract signals
                payload = await self.extract_fingerprints()
                
                # Upload to server
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{self.server_url}/api/v1/captures",
                        json=payload.model_dump(),
                        timeout=10.0
                    )
                    
                    if resp.status_code == 200:
                        result = resp.json()
                        logger.info(f"Upload OK. Capture ID: {result['capture_id']}")
                    else:
                        logger.error(f"Server error: {resp.status_code}")
                
            except httpx.ConnectError:
                logger.error("Cannot reach server. Will retry...")
            except Exception as e:
                logger.error(f"Capture cycle error: {e}")
            
            await asyncio.sleep(self.capture_interval)
    
    async def extract_fingerprints(self) -> FingerprintPayload:
        """Get fingerprints from hardware or simulation"""
        if get_settings().edge_mode == "pi":
            return await self._extract_from_hardware()
        else:
            return await self._extract_simulated()
    
    async def _extract_from_hardware(self) -> FingerprintPayload:
        """Real Pi: HDMI capture + processing"""
        # Pseudocode:
        # 1. Use ffmpeg to capture HDMI to /tmp/frame.jpg
        # 2. Run OpenCV feature extraction
        # 3. Run Tesseract OCR
        # 4. Use librosa for audio fingerprint
        pass
    
    async def _extract_simulated(self) -> FingerprintPayload:
        """Simulation mode: random fingerprints"""
        return FingerprintPayload(
            device_id=self.device_id,
            timestamp=datetime.now(UTC),
            visual_fp=(np.random.rand(64) * 0.5).tolist(),
            audio_fp=f"simulated-audio-{int(time.time())}",
            logo_fp=(np.random.rand(16) * 0.5).tolist(),
            ocr_text="simulated ocr text",
            snapshot_url=f"{self.snapshot_base_url}/{self.device_id}/frame.jpg"
        )
```

## 8. DATABASE SESSION MANAGEMENT

**File:** `src/content_platform/server/db.py`

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

settings = get_settings()

# Connection pooling
engine = create_engine(
    settings.database_url,
    future=True,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
    pool_size=20,          # Connection pool size
    max_overflow=40        # Additional overflow connections
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()

def get_db() -> Generator[Session, None, None]:
    """Dependency for FastAPI routes"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

## 9. TESTING PATTERN

**File:** `tests/test_api.py`

```python
from fastapi.testclient import TestClient
from content_platform.server.main import app

def test_ingest_capture_returns_recognition_result() -> None:
    with TestClient(app) as client:
        # 1. Send capture payload
        response = client.post(
            "/api/v1/captures",
            json={
                "device_id": "PI001",
                "timestamp": datetime.now(UTC).isoformat(),
                "visual_fp": [0.88, 0.23, 0.61, 0.79] + [0.0] * 60,
                "audio_fp": "dilwale-main-track",
                "logo_fp": [0.95, 0.15, 0.41, 0.81] + [0.0] * 12,
                "ocr_text": "dilwale shah rukh kajol sony max",
                "snapshot_url": "http://localhost/snapshot.jpg",
            },
        )

        # 2. Verify immediate response
        assert response.status_code == 200
        body = response.json()
        assert body["result"]["content_name"] == "Matching in progress..."
        
        # 3. Wait for background task
        time.sleep(0.5)
        
        # 4. Verify final result
        caps = client.get("/api/v1/captures").json()["items"]
        latest = next(c for c in caps if c["id"] == body["capture_id"])
        assert latest["result"]["content_name"] == "Dilwale"
        assert latest["result"]["confidence"] >= 0.8
```

## 10. DEPLOYMENT CONFIGURATION

**File:** `pyproject.toml` / Environment

```toml
[project.scripts]
crp-server = "content_platform.server.main:run"
crp-edge = "content_platform.edge.main:run"

# Environment variables
CRP_DATABASE_URL=sqlite:///./content_platform.db
CRP_SERVER_HOST=0.0.0.0
CRP_SERVER_PORT=8000
CRP_EDGE_SERVER_URL=http://127.0.0.1:8000
CRP_DEVICE_ID=PI001
CRP_CAPTURE_INTERVAL_SECONDS=10
CRP_QDRANT_URL=http://localhost:6333
CRP_EDGE_MODE=simulated
```

---

## USAGE PATTERNS

### Creating a new API endpoint:
1. Define Pydantic model in `shared/models.py`
2. Add function to `server/main.py` with `@app.get/post/etc` decorator
3. Use `db: Session = Depends(get_db)` for database access
4. Return JSON-serializable response
5. Add test in `tests/test_*.py`

### Modifying similarity scoring:
1. Adjust weights in `WeightedScores.final` property
2. Tune thresholds in `build_result()` (currently 0.55)
3. Run `pytest tests/test_matching.py` to verify
4. Test with real captures via API

### Adding new content type:
1. Add category to `ContentLibrary.category` enum
2. Update `DEFAULT_LIBRARY` in `library.py`
3. Reseed database: `python -c "from content_platform.server.library import seed_reference_library; seed_reference_library(SessionLocal())"`
4. Verify in `/api/v1/library` endpoint

