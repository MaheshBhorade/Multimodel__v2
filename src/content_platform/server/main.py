from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import json
from pathlib import Path
import uuid

import uvicorn
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel

from content_platform.server.db import Base, SessionLocal, engine, get_db
from content_platform.server.library import seed_reference_library
from content_platform.server.matching import match_content, initialize_faiss_indexes
from content_platform.server.models import Capture, Device, RecognitionResultRecord, ContentLibrary, PlaybackSession
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
        initialize_faiss_indexes(db)
    yield


app = FastAPI(title="Content Recognition Platform", version="0.1.0", lifespan=lifespan)
app.mount("/snapshots", StaticFiles(directory=SNAPSHOT_ROOT, check_dir=False), name="snapshots")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


import logging
logger = logging.getLogger(__name__)


def process_matching_background(capture_ids: list[int], payload: FingerprintPayload) -> None:
    with open("debug_bg.txt", "a") as f_dbg:
        f_dbg.write(f"Background task started for captures: {capture_ids}\n")
    with SessionLocal() as db:
        try:
            initialize_faiss_indexes(db)
            for i, capture_id in enumerate(capture_ids):
                with open("debug_bg.txt", "a") as f_dbg:
                    f_dbg.write(f"Processing capture: {capture_id}\n")
                capture = db.query(Capture).filter(Capture.id == capture_id).first()
                if not capture:
                    with open("debug_bg.txt", "a") as f_dbg:
                        f_dbg.write(f"Capture {capture_id} not found in DB!\n")
                    continue
                
                # Reconstruct a single-second payload for this specific frame
                single_payload = FingerprintPayload(
                    device_id=payload.device_id,
                    timestamp=capture.captured_at,
                    visual_fps=json.loads(capture.visual_fp),
                    audio_fp=payload.audio_fp,
                    snapshot_url=capture.snapshot_url,
                    batch_count=1,
                    batch_duration_sec=1,
                    best_frame_index=0,
                    confidence_visual=payload.confidence_visual,
                    confidence_audio=payload.confidence_audio
                )
                
                result = match_content(db, single_payload)
                with open("debug_bg.txt", "a") as f_dbg:
                    f_dbg.write(f"Matched capture {capture_id} to: {result.content_name} ({result.content_type})\n")
                
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
            with open("debug_bg.txt", "a") as f_dbg:
                f_dbg.write(f"Successfully committed transaction for captures: {capture_ids}\n")
            
            # Rebuild playback sessions for the device once
            if capture_ids:
                first_capture = db.query(Capture).filter(Capture.id == capture_ids[0]).first()
                if first_capture and first_capture.device:
                    try:
                        from content_platform.server.temporal import rebuild_playback_sessions_for_device
                        rebuild_playback_sessions_for_device(db, first_capture.device.device_id)
                        with open("debug_bg.txt", "a") as f_dbg:
                            f_dbg.write(f"Successfully rebuilt playback sessions for device: {first_capture.device.device_id}\n")
                    except Exception as se:
                        logger.error("Failed to rebuild playback sessions for device %s: %s", first_capture.device.device_id, se)
                        with open("debug_bg.txt", "a") as f_dbg:
                            f_dbg.write(f"Failed to rebuild sessions: {se}\n")
        except Exception as e:
            logger.error("Failed to match captures in background: %s", e)
            with open("debug_bg.txt", "a") as f_dbg:
                f_dbg.write(f"Error during matching: {e}\n")
                import traceback
                traceback.print_exc(file=f_dbg)
            try:
                for capture_id in capture_ids:
                    capture = db.query(Capture).filter(Capture.id == capture_id).first()
                    if capture and capture.status == "pending":
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

    from datetime import timedelta
    created_captures = []
    
    # We will create individual captures (one for each second of the batch)
    num_frames = len(payload.visual_fps) if payload.visual_fps else 1
    
    for i in range(num_frames):
        captured_at = payload.timestamp + timedelta(seconds=i)
        
        # Check if this frame is the one that has the snapshot
        snapshot_url = payload.snapshot_url if i == payload.best_frame_index else None
        
        vis_fp = [payload.visual_fps[i]] if payload.visual_fps else []
        
        capture = Capture(
            device_id=device.id,
            captured_at=captured_at,
            visual_fp=json.dumps(vis_fp),
            audio_fp=json.dumps(payload.audio_fp),
            logo_fp="[]",
            ocr_text="",
            snapshot_url=snapshot_url,
            status="pending",
        )
        db.add(capture)
        created_captures.append(capture)
        
    db.commit()

    capture_ids = [c.id for c in created_captures]
    background_tasks.add_task(process_matching_background, capture_ids, payload)

    temp_result = RecognitionResult(
        content_name="Matching in progress...",
        content_type="unknown",
        confidence=0.0,
        breakdown=MatchBreakdown(visual_score=0.0, audio_score=0.0, ocr_score=0.0, logo_score=0.0),
        matched_channel=None
    )
    
    # Return the capture corresponding to the best_frame_index, or the first one
    return_capture = created_captures[payload.best_frame_index] if payload.best_frame_index < len(created_captures) else created_captures[0]
    return CaptureResponse(capture_id=return_capture.id, result=temp_result)


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
            "last_active": (r.last_active.isoformat() + "Z") if r.last_active else None,
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
        query.options(joinedload(Capture.device), joinedload(Capture.result))
        .order_by(Capture.captured_at.desc())
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
                "captured_at": c.captured_at.isoformat() + "Z",
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
    
    # Try to unpack the 2D visual fingerprint list if nested, to ensure library gets a 1D list
    try:
        vis_list = json.loads(capture.visual_fp)
        if vis_list and isinstance(vis_list[0], list):
            visual_fp_val = vis_list[0]
            visual_fp_str = json.dumps(visual_fp_val)
        else:
            visual_fp_val = vis_list
            visual_fp_str = capture.visual_fp
    except Exception:
        visual_fp_val = []
        visual_fp_str = capture.visual_fp
    
    # Create content library entry
    library_item = ContentLibrary(
        external_content_id=external_content_id,
        title=payload.title,
        category=payload.category,
        channel_name=payload.channel_name,
        visual_fp=visual_fp_str,
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
        visual_fp_val,
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
    
    # Rebuild playback sessions for the device
    try:
        from content_platform.server.temporal import rebuild_playback_sessions_for_device
        rebuild_playback_sessions_for_device(db, capture.device.device_id)
    except Exception as se:
        logger.error("Failed to rebuild playback sessions for device %s after resolve: %s", capture.device.device_id, se)
        
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
    # Get last 20 matched captures in chronological order (optimized, no subquery)
    results = (
        db.query(Capture)
        .options(joinedload(Capture.device), joinedload(Capture.result))
        .join(RecognitionResultRecord, Capture.id == RecognitionResultRecord.capture_id)
        .order_by(Capture.captured_at.desc())
        .limit(20)
        .all()
    )
    # Sort chronologically ascending
    results.reverse()
    return [
        {
            "timestamp": c.captured_at.isoformat() + "Z",
            "content_name": c.result.content_name,
            "category": c.result.content_type,
            "device_id": c.device.device_id if c.device else "Unknown",
        }
        for c in results if c.result
    ]


@app.get("/api/v1/analytics/sessions")
def get_analytics_sessions(db: Session = Depends(get_db)):
    # Get last 20 playback sessions in chronological order
    sessions = (
        db.query(PlaybackSession)
        .order_by(PlaybackSession.start_time.desc())
        .limit(20)
        .all()
    )
    sessions.reverse()
    return [
        {
            "id": s.id,
            "device_id": s.device_id,
            "content_name": s.content_name,
            "category": s.content_type,
            "start_time": s.start_time.isoformat() + "Z",
            "end_time": s.end_time.isoformat() + "Z",
            "duration_seconds": s.duration_seconds,
            "entry_count": s.entry_count,
        }
        for s in sessions
    ]


import csv
import io
from fastapi.responses import StreamingResponse

@app.get("/api/v1/analytics/sessions/export")
def export_sessions_csv(db: Session = Depends(get_db)):
    sessions = (
        db.query(PlaybackSession)
        .order_by(PlaybackSession.start_time.desc())
        .all()
    )
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Session ID", "Device ID", "Content Name", "Category", 
        "Start Time", "End Time", "Duration (seconds)", "Consecutive Detections"
    ])
    
    for s in sessions:
        writer.writerow([
            s.id,
            s.device_id,
            s.content_name,
            s.content_type,
            s.start_time.isoformat() + "Z",
            s.end_time.isoformat() + "Z",
            s.duration_seconds,
            s.entry_count
        ])
        
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=playback_sessions.csv"}
    )


@app.get("/api/v1/captures/export")
def export_captures_csv(db: Session = Depends(get_db)):
    captures = (
        db.query(Capture)
        .outerjoin(RecognitionResultRecord, Capture.id == RecognitionResultRecord.capture_id)
        .order_by(Capture.captured_at.desc())
        .all()
    )
    
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Capture ID", "Device ID", "Timestamp", "Status", 
        "Content Name", "Category", "Confidence", 
        "Visual Score", "Audio Score", "OCR Score", "Logo Score"
    ])
    
    for c in captures:
        content_name = c.result.content_name if c.result else "Unknown"
        category = c.result.content_type if c.result else "unknown"
        confidence = c.result.confidence if c.result else 0.0
        v_score = c.result.visual_score if c.result else 0.0
        a_score = c.result.audio_score if c.result else 0.0
        o_score = c.result.ocr_score if c.result else 0.0
        l_score = c.result.logo_score if c.result else 0.0
        
        writer.writerow([
            c.id,
            c.device.device_id if c.device else "Unknown",
            c.captured_at.isoformat() + "Z",
            c.status,
            content_name,
            category,
            confidence,
            v_score,
            a_score,
            o_score,
            l_score
        ])
        
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=realtime_captures.csv"}
    )


# Mount static files for dashboard last to avoid blocking API routes
app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")


def run() -> None:
    uvicorn.run(
        "content_platform.server.main:app",
        host=settings.server_host,
        port=settings.server_port,
        reload=False,
    )
