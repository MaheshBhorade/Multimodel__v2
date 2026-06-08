from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
from pathlib import Path
import uuid

import uvicorn
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from pydantic import BaseModel

from content_platform.server.db import Base, SessionLocal, engine, get_db
from content_platform.server.library import seed_reference_library
from content_platform.server.matching import match_content
from content_platform.server.models import Capture, Device, RecognitionResultRecord, ContentLibrary
from content_platform.shared.config import get_settings
from content_platform.shared.models import CaptureResponse, FingerprintPayload, SnapshotUploadResponse

settings = get_settings()
SNAPSHOT_ROOT = Path("runtime/snapshots")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    Base.metadata.create_all(bind=engine)
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    (Path(__file__).parent / "static").mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        seed_reference_library(db)
    yield


app = FastAPI(title="Content Recognition Platform", version="0.1.0", lifespan=lifespan)
app.mount("/snapshots", StaticFiles(directory=SNAPSHOT_ROOT, check_dir=False), name="snapshots")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


import logging
logger = logging.getLogger(__name__)


def process_matching_background(capture_id: int, payload: FingerprintPayload) -> None:
    with SessionLocal() as db:
        try:
            capture = db.query(Capture).filter(Capture.id == capture_id).first()
            if not capture:
                return
            
            result = match_content(db, payload)
            
            # Add recognition record
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
            logger.info("Background matching complete for capture_id=%d. Content=%s", capture_id, result.content_name)
        except Exception as e:
            logger.error("Failed to match capture in background: %s", e)
            try:
                capture = db.query(Capture).filter(Capture.id == capture_id).first()
                if capture:
                    capture.status = "failed"
                    db.commit()
            except Exception:
                pass


from content_platform.shared.models import RecognitionResult, MatchBreakdown


@app.post("/api/v1/captures", response_model=CaptureResponse)
def ingest_capture(
    payload: FingerprintPayload,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db)
) -> CaptureResponse:
    device = db.scalar(select(Device).where(Device.device_id == payload.device_id))
    if device is None:
        device = Device(device_id=payload.device_id, status="active")
        db.add(device)
        db.flush()

    capture = Capture(
        device_id=device.id,
        captured_at=payload.timestamp,
        visual_fp=json.dumps(payload.visual_fps),
        audio_fp=json.dumps(payload.audio_fp),
        logo_fp="[]",
        ocr_text="",
        snapshot_url=payload.snapshot_url,
        status="pending",
    )
    db.add(capture)
    db.commit()

    background_tasks.add_task(process_matching_background, capture.id, payload)

    temp_result = RecognitionResult(
        content_name="Matching in progress...",
        content_type="unknown",
        confidence=0.0,
        breakdown=MatchBreakdown(visual_score=0.0, audio_score=0.0, ocr_score=0.0, logo_score=0.0),
        matched_channel=None
    )
    return CaptureResponse(capture_id=capture.id, result=temp_result)


@app.post("/api/v1/snapshots", response_model=SnapshotUploadResponse)
async def upload_snapshot(
    request: Request,
    device_id: str = Form(...),
    timestamp: str = Form(...),
    file: UploadFile = File(...),
) -> SnapshotUploadResponse:
    device_dir = SNAPSHOT_ROOT / device_id
    device_dir.mkdir(parents=True, exist_ok=True)
    safe_name = f"{timestamp.replace(':', '-').replace('.', '-')}_{file.filename}"
    target = device_dir / safe_name
    target.write_bytes(await file.read())
    return SnapshotUploadResponse(snapshot_url=str(request.base_url).rstrip("/") + f"/snapshots/{device_id}/{safe_name}")


@app.get("/api/v1/devices")
def list_devices(db: Session = Depends(get_db)):
    results = (
        db.query(
            Device.device_id,
            Device.status,
            Device.location,
            func.count(Capture.id).label("capture_count"),
            func.max(Capture.captured_at).label("last_active")
        )
        .outerjoin(Capture, Device.id == Capture.device_id)
        .group_by(Device.id)
        .all()
    )
    return [
        {
            "device_id": r.device_id,
            "status": r.status,
            "location": r.location,
            "capture_count": r.capture_count,
            "last_active": r.last_active.isoformat() if r.last_active else None,
        }
        for r in results
    ]


@app.get("/api/v1/captures")
def list_captures(
    device_id: str | None = None,
    only_unknown: bool = False,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    query = db.query(Capture).outerjoin(RecognitionResultRecord, Capture.id == RecognitionResultRecord.capture_id)
    if device_id:
        query = query.join(Device, Device.id == Capture.device_id).filter(Device.device_id == device_id)
    if only_unknown:
        query = query.filter(RecognitionResultRecord.content_type == "unknown")
    
    total = query.count()
    captures = (
        query.order_by(Capture.captured_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    
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
                "ocr_text": c.ocr_text,
                "audio_fp": json.loads(c.audio_fp) if c.audio_fp else [],
                "visual_fp": json.loads(c.visual_fp) if c.visual_fp else [],
                "logo_fp": json.loads(c.logo_fp) if c.logo_fp else [],
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


@app.get("/api/v1/library")
def list_library(db: Session = Depends(get_db)):
    items = db.query(ContentLibrary).all()
    return [
        {
            "id": item.id,
            "external_content_id": item.external_content_id,
            "title": item.title,
            "category": item.category,
            "channel_name": item.channel_name,
            "visual_fp": json.loads(item.visual_fp) if item.visual_fp else [],
            "logo_fp": json.loads(item.logo_fp) if item.logo_fp else [],
            "audio_fp": json.loads(item.audio_fp) if item.audio_fp else [],
            "ocr_keywords": item.ocr_keywords,
        }
        for item in items
    ]


class LibraryCreatePayload(BaseModel):
    title: str
    category: str
    channel_name: str | None = None
    visual_fp: list[float]
    audio_fp: list[float]
    logo_fp: list[float]
    ocr_keywords: str


@app.post("/api/v1/library")
def create_library_item(payload: LibraryCreatePayload, db: Session = Depends(get_db)):
    from content_platform.server.vector_store import vector_store
    external_content_id = f"{payload.category}-{str(uuid.uuid4())[:8]}"
    item = ContentLibrary(
        external_content_id=external_content_id,
        title=payload.title,
        category=payload.category,
        channel_name=payload.channel_name,
        visual_fp=json.dumps(payload.visual_fp),
        audio_fp=json.dumps(payload.audio_fp),
        logo_fp=json.dumps(payload.logo_fp),
        ocr_keywords=payload.ocr_keywords,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    
    # Sync to Qdrant
    vector_store.upsert_reference(external_content_id, payload.visual_fp, payload.logo_fp)
    
    return {
        "id": item.id,
        "external_content_id": item.external_content_id,
        "title": item.title,
        "category": item.category,
        "channel_name": item.channel_name,
        "visual_fp": json.loads(item.visual_fp),
        "logo_fp": json.loads(item.logo_fp),
        "audio_fp": item.audio_fp,
        "ocr_keywords": item.ocr_keywords,
    }


@app.delete("/api/v1/library/{item_id}")
def delete_library_item(item_id: int, db: Session = Depends(get_db)):
    from content_platform.server.vector_store import vector_store
    item = db.query(ContentLibrary).filter(ContentLibrary.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Library item not found")
    
    # Remove from Qdrant
    vector_store.delete_reference(item.external_content_id)
    
    db.delete(item)
    db.commit()
    return {"status": "deleted"}


class CaptureResolvePayload(BaseModel):
    title: str
    category: str
    channel_name: str | None = None


@app.post("/api/v1/captures/{capture_id}/resolve")
def resolve_capture(capture_id: int, payload: CaptureResolvePayload, db: Session = Depends(get_db)):
    capture = db.query(Capture).filter(Capture.id == capture_id).first()
    if not capture:
        raise HTTPException(status_code=404, detail="Capture not found")
        
    external_content_id = f"manual-{payload.category}-{str(uuid.uuid4())[:8]}"
    
    # Create content library entry
    library_item = ContentLibrary(
        external_content_id=external_content_id,
        title=payload.title,
        category=payload.category,
        channel_name=payload.channel_name,
        visual_fp=capture.visual_fp,
        audio_fp=capture.audio_fp,
        logo_fp=capture.logo_fp,
        ocr_keywords=capture.ocr_text,
    )
    db.add(library_item)
    db.flush()

    # Sync to Qdrant
    from content_platform.server.vector_store import vector_store
    vector_store.upsert_reference(
        external_content_id,
        json.loads(capture.visual_fp) if capture.visual_fp else [],
        json.loads(capture.logo_fp) if capture.logo_fp else [],
    )
    
    # Update recognition result record
    if capture.result:
        capture.result.content_name = payload.title
        capture.result.content_type = payload.category
        capture.result.confidence = 1.0
        capture.result.visual_score = 1.0
        capture.result.audio_score = 1.0
        capture.result.ocr_score = 1.0
        capture.result.logo_score = 1.0
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


@app.get("/api/v1/analytics/overview")
def get_analytics_overview(db: Session = Depends(get_db)):
    total_captures = db.query(Capture).count()
    active_devices = db.query(Device).filter(Device.status == "active").count()
    pending_reviews = (
        db.query(Capture)
        .join(RecognitionResultRecord, Capture.id == RecognitionResultRecord.capture_id)
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
    results = (
        db.query(
            RecognitionResultRecord.content_type,
            func.count(RecognitionResultRecord.id).label("count")
        )
        .group_by(RecognitionResultRecord.content_type)
        .all()
    )
    return {r.content_type: r.count for r in results}


@app.get("/api/v1/analytics/ad-frequency")
def get_analytics_ad_frequency(db: Session = Depends(get_db)):
    results = (
        db.query(
            RecognitionResultRecord.content_name,
            func.count(RecognitionResultRecord.id).label("count")
        )
        .filter(RecognitionResultRecord.content_type == "advertisement")
        .group_by(RecognitionResultRecord.content_name)
        .order_by(func.count(RecognitionResultRecord.id).desc())
        .limit(5)
        .all()
    )
    return [{"name": r.content_name, "count": r.count} for r in results]


@app.get("/api/v1/analytics/timeline")
def get_analytics_timeline(db: Session = Depends(get_db)):
    # Get last 20 matched captures in chronological order
    subquery = (
        db.query(Capture.id)
        .join(RecognitionResultRecord, Capture.id == RecognitionResultRecord.capture_id)
        .order_by(Capture.captured_at.desc())
        .limit(20)
        .subquery()
    )
    results = (
        db.query(Capture)
        .join(RecognitionResultRecord, Capture.id == RecognitionResultRecord.capture_id)
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


# Mount static files for dashboard last to avoid blocking API routes
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")


def run() -> None:
    uvicorn.run(
        "content_platform.server.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=False,
    )
