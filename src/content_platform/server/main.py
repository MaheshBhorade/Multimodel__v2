import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import csv
from datetime import datetime, timedelta
import io
import json
from pathlib import Path
import uuid

import uvicorn
from fastapi import Depends, FastAPI, File, Form, Request, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func
from sqlalchemy.orm import Session, joinedload
from pydantic import BaseModel

from content_platform.server.db import Base, SessionLocal, engine, get_db, PlatformBase, platform_engine, PlatformSessionLocal, get_platform_db
from content_platform.server.library import seed_reference_library
from content_platform.server.matching import match_content, initialize_faiss_indexes, match_platform, initialize_platform_faiss_index
from content_platform.server.models import Capture, Device, RecognitionResultRecord, Content, ContentSegment, PlaybackSession
from content_platform.server.confidence_tuning import LOGO_ROI_MIN_STD
from content_platform.shared.config import get_settings
from content_platform.shared.models import CaptureResponse, FingerprintPayload, SnapshotUploadResponse

settings = get_settings()
SNAPSHOT_ROOT = Path("runtime/snapshots")

main_loop = None

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[asyncio.Queue] = []
    
    async def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self.active_connections.append(q)
        return q
        
    def unsubscribe(self, q: asyncio.Queue):
        if q in self.active_connections:
            self.active_connections.remove(q)
            
    async def broadcast(self, data: dict):
        for q in list(self.active_connections):
            await q.put(data)

manager = ConnectionManager()

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    global main_loop
    main_loop = asyncio.get_running_loop()
    
    # Configure SQLite journal mode to WAL once at startup
    if settings.database_url.startswith("sqlite"):
        with engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
    if settings.platform_database_url.startswith("sqlite"):
        with platform_engine.connect() as conn:
            conn.exec_driver_sql("PRAGMA journal_mode=WAL")
            
    Base.metadata.create_all(bind=engine)
    PlatformBase.metadata.create_all(bind=platform_engine)
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    (Path(__file__).parent / "static").mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        seed_reference_library(db)
        initialize_faiss_indexes(db)
    with PlatformSessionLocal() as db_platform:
        initialize_platform_faiss_index(db_platform)
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
    with SessionLocal() as db, PlatformSessionLocal() as db_platform:
        try:
            initialize_faiss_indexes(db)
            initialize_platform_faiss_index(db_platform)
            # Server-side logo extraction from the snapshot if available
            logo_fp = None
            if payload.snapshot_url:
                try:
                    import cv2
                    from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor
                    parts = payload.snapshot_url.split("/snapshots/")
                    if len(parts) > 1:
                        local_path = SNAPSHOT_ROOT / parts[1]
                        if local_path.exists():
                            img = cv2.imread(str(local_path))
                            if img is not None:
                                h, w = img.shape[:2]
                                ymin, ymax = int(h * 0.75), int(h * 0.95)
                                xmin, xmax = int(w * 0.75), int(w * 0.98)
                                logo_roi = img[ymin:ymax, xmin:xmax]
                                if logo_roi is not None and logo_roi.size > 0:
                                    import numpy as np
                                    if np.std(logo_roi) >= LOGO_ROI_MIN_STD:
                                        logo_fp = DeepEmbeddingsExtractor.extract_visual(logo_roi)
                                    else:
                                        logger.info(f"Skipping uniform/blank logo region (std: {np.std(logo_roi):.2f})")
                except Exception as le:
                    logger.warning(f"Server-side logo extraction failed: {le}")

            # Initialize last known platform for the device
            last_known_platform = "unknown"
            if capture_ids:
                first_cap = db.query(Capture).filter(Capture.id == capture_ids[0]).first()
                if first_cap:
                    last_rec = (
                        db.query(RecognitionResultRecord)
                        .join(Capture, Capture.id == RecognitionResultRecord.capture_id)
                        .filter(Capture.device_id == first_cap.device_id)
                        .filter(RecognitionResultRecord.matched_platform != "unknown")
                        .filter(RecognitionResultRecord.matched_platform != "global")
                        .order_by(Capture.captured_at.desc())
                        .first()
                    )
                    if last_rec and (first_cap.captured_at - last_rec.capture.captured_at).total_seconds() <= 300:
                        last_known_platform = last_rec.matched_platform

            for i, capture_id in enumerate(capture_ids):
                with open("debug_bg.txt", "a") as f_dbg:
                    f_dbg.write(f"Processing capture: {capture_id}\n")
                capture = db.query(Capture).filter(Capture.id == capture_id).first()
                if not capture:
                    with open("debug_bg.txt", "a") as f_dbg:
                        f_dbg.write(f"Capture {capture_id} not found in DB!\n")
                    continue
                
                if logo_fp:
                    capture.logo_fp = json.dumps(logo_fp)

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
                
                matched_plat = match_platform(db_platform, single_payload, logo_fp=logo_fp)
                direct_plat_match = matched_plat != "unknown"
                if matched_plat == "unknown":
                    if logo_fp is not None:
                        # Clear last known platform since logo matched explicitly as unknown
                        last_known_platform = "unknown"
                    else:
                        # Fallback to last known platform only when no snapshot logo is available
                        matched_plat = last_known_platform
                else:
                    last_known_platform = matched_plat

                # If an OTT platform homepage layout is directly detected, we are on the UI/dashboard.
                # Do not run content matching to prevent false detections from recommended thumbnails.
                OTT_PLATFORMS = {"youtube", "uplay", "netflix", "prime_video", "sonyliv", "jiohotstar", "zee5", "ott"}
                if direct_plat_match and matched_plat in OTT_PLATFORMS:
                    content_result = match_content(db, single_payload, platform_id=matched_plat)
                    if content_result and content_result.confidence >= 0.55:
                        result = content_result
                    else:
                        from content_platform.shared.models import RecognitionResult, MatchBreakdown
                        result = RecognitionResult(
                            content_name="Unknown Content",
                            content_type="unknown",
                            confidence=0.0,
                            breakdown=MatchBreakdown(
                                visual_score=content_result.breakdown.visual_score if content_result else 0.0,
                                audio_score=content_result.breakdown.audio_score if content_result else 0.0,
                                ocr_score=content_result.breakdown.ocr_score if content_result else 0.0,
                                logo_score=content_result.breakdown.logo_score if content_result else 0.0
                            ),
                            matched_channel=None,
                            playback_position=None
                        )
                else:
                    result = match_content(db, single_payload, platform_id=matched_plat)

                # Calculate actual logo similarity score if channel matched
                final_logo_score = 0.0
                if logo_fp and matched_plat and matched_plat != "unknown":
                    try:
                        from content_platform.server.models import PlatformReference
                        from content_platform.server.matching import cosine_like_similarity
                        ref_plats = db_platform.query(PlatformReference).filter(PlatformReference.platform_id == matched_plat).all()
                        best_sim = 0.0
                        for ref in ref_plats:
                            ref_logo = json.loads(ref.logo_fp)
                            sim = cosine_like_similarity(logo_fp, ref_logo)
                            if sim > best_sim:
                                best_sim = sim
                        final_logo_score = best_sim
                    except Exception as lse:
                        logger.warning(f"Failed to calculate logo score: {lse}")

                with open("debug_bg.txt", "a") as f_dbg:
                    f_dbg.write(f"Matched capture {capture_id} to: {result.content_name} ({result.content_type}) | Platform: {matched_plat}\n")
                
                # Resolve final platform
                final_platform = result.platform
                if final_platform in ("global", "unknown", None):
                    if matched_plat not in ("unknown", "global", None):
                        final_platform = matched_plat
                    else:
                        final_platform = final_platform or matched_plat or "unknown"

                # Add recognition record
                record = RecognitionResultRecord(
                    capture_id=capture.id,
                    content_id=result.content_id,
                    content_name=result.content_name,
                    content_type=result.content_type,
                    confidence=result.confidence,
                    visual_score=result.breakdown.visual_score,
                    audio_score=result.breakdown.audio_score,
                    ocr_score=result.breakdown.ocr_score,
                    logo_score=final_logo_score,
                    matched_platform=final_platform,
                    matched_channel=result.matched_channel,
                    series=result.series,
                    season=result.season,
                    episode=result.episode,
                    playback_position=result.playback_position,
                )
                db.add(record)
                
                # Log final match
                match_log = (
                    f"MATCH COMPLETE\n"
                    f"capture={capture.id}\n"
                    f"content={result.content_name}\n"
                    f"confidence={result.confidence:.4f}\n"
                    f"visual={result.breakdown.visual_score:.4f}\n"
                    f"audio={result.breakdown.audio_score:.4f}\n"
                    f"logo={final_logo_score:.4f}"
                )
                print(match_log, flush=True)
                logger.info(match_log)
                
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
                        from content_platform.server.temporal import rebuild_playback_sessions_for_device, smooth_recent_captures_for_device
                        smooth_recent_captures_for_device(db, first_capture.device.device_id)
                        rebuild_playback_sessions_for_device(db, first_capture.device.device_id)
                        with open("debug_bg.txt", "a") as f_dbg:
                            f_dbg.write(f"Successfully rebuilt playback sessions for device: {first_capture.device.device_id}\n")
                    except Exception as se:
                        logger.error("Failed to rebuild playback sessions for device %s: %s", first_capture.device.device_id, se)
                        with open("debug_bg.txt", "a") as f_dbg:
                            f_dbg.write(f"Failed to rebuild sessions: {se}\n")
            
            # Broadcast the update event to all active SSE clients
            if main_loop and main_loop.is_running():
                asyncio.run_coroutine_threadsafe(manager.broadcast({"type": "update"}), main_loop)
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


@app.delete("/api/v1/devices/{device_id}")
def delete_device(device_id: str, db: Session = Depends(get_db)):
    device = db.query(Device).filter(Device.device_id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    db.delete(device)
    db.commit()
    return {"status": "success", "message": f"Device {device_id} deleted successfully"}


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
                    "matched_platform": c.result.matched_platform,
                    "matched_channel": c.result.matched_channel,
                    "series": c.result.series,
                    "season": c.result.season,
                    "episode": c.result.episode,
                    "playback_position": c.result.playback_position,
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


@app.get("/api/v1/captures/{capture_id}/result", response_model=RecognitionResult)
def get_capture_result(capture_id: int, db: Session = Depends(get_db)) -> RecognitionResult:
    from content_platform.server.models import RecognitionResultRecord
    from content_platform.shared.models import RecognitionResult, MatchBreakdown
    record = db.query(RecognitionResultRecord).filter(RecognitionResultRecord.capture_id == capture_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Result not found for capture")
        
    # Map custom categories if not present in allowed types
    content_type = record.content_type
    allowed_types = ["channel", "advertisement", "movie", "series", "episode", "ott", "music", "unknown"]
    if content_type not in allowed_types:
        if content_type == "song":
            content_type = "music"
        else:
            content_type = "unknown"

    return RecognitionResult(
        content_id=record.content_id,
        content_name=record.content_name,
        content_type=content_type,
        confidence=record.confidence,
        breakdown=MatchBreakdown(
            visual_score=record.visual_score,
            audio_score=record.audio_score,
            ocr_score=record.ocr_score,
            logo_score=record.logo_score
        ),
        matched_channel=record.matched_channel,
        playback_position=record.playback_position,
        platform=record.matched_platform,
        series=record.series,
        season=record.season,
        episode=record.episode
    )


@app.get("/api/v1/library")
def list_library(db: Session = Depends(get_db)):
    from sqlalchemy.sql import func
    
    # Query all Content items
    contents = db.query(Content).all()
    content_map = {c.content_id: c for c in contents}
    
    # Query minimum segment_id per content_id
    min_ids_query = select(func.min(ContentSegment.segment_id)).group_by(ContentSegment.content_id)
    min_ids = db.scalars(min_ids_query).all()
    
    # Fetch minimum segment metadata only (avoiding loading full ORM objects and huge strings)
    segments_query = (
        select(
            ContentSegment.segment_id,
            ContentSegment.content_id,
            ContentSegment.visual_fp,
            ContentSegment.audio_fp
        )
        .where(ContentSegment.segment_id.in_(min_ids))
    )
    segments = db.execute(segments_query).all()
    
    results = []
    for seg_id, content_id, visual_fp_str, audio_fp_str in segments:
        content = content_map.get(content_id)
        if not content:
            continue
            
        # Parse first few characters to extract the first 4 floats efficiently
        try:
            prefix = visual_fp_str[:120].rsplit(',', 1)[0] + ']'
            visual_fp = json.loads(prefix)[:4]
        except Exception:
            try:
                visual_fp = json.loads(visual_fp_str)[:4] if visual_fp_str else []
            except Exception:
                visual_fp = []
                
        try:
            prefix = audio_fp_str[:120].rsplit(',', 1)[0] + ']'
            audio_fp = json.loads(prefix)[:4]
        except Exception:
            try:
                audio_fp = json.loads(audio_fp_str)[:4] if audio_fp_str else []
            except Exception:
                audio_fp = []
                
        results.append({
            "id": seg_id,
            "external_content_id": content_id,
            "title": content.title,
            "category": content.content_type,
            "channel_name": content.platform_id,
            "visual_fp": visual_fp,
            "logo_fp": [],
            "audio_fp": audio_fp,
            "ocr_keywords": "",
            "platform": content.platform_id,
            "series": content.series_name,
            "season": content.season_number,
            "episode": content.episode_number,
        })
    return results


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
    from content_platform.server.matching import parse_title_metadata
    plat_val, chan_val, series_val, season_val, episode_val = parse_title_metadata(payload.title, payload.category)
    
    external_content_id = f"{payload.category}-{str(uuid.uuid4())[:8]}"
    content = Content(
        content_id=external_content_id,
        title=payload.title,
        content_type=payload.category,
        platform_id=payload.channel_name or plat_val or chan_val,
        series_name=series_val,
        season_number=season_val,
        episode_number=episode_val
    )
    db.add(content)
    db.flush()

    item = ContentSegment(
        content_id=external_content_id,
        segment_index=0,
        segment_offset=0,
        visual_fp=json.dumps(payload.visual_fp),
        audio_fp=json.dumps(payload.audio_fp)
    )
    db.add(item)
    db.commit()
    db.refresh(content)
    db.refresh(item)
    
    # Sync to Qdrant
    vector_store.upsert_reference(external_content_id, payload.visual_fp, payload.logo_fp)
    
    return {
        "id": item.segment_id,
        "external_content_id": content.content_id,
        "title": content.title,
        "category": content.content_type,
        "channel_name": content.platform_id,
        "visual_fp": json.loads(item.visual_fp),
        "logo_fp": json.loads(item.visual_fp),
        "audio_fp": item.audio_fp,
        "ocr_keywords": "",
        "platform": content.platform_id,
        "series": content.series_name,
        "season": content.season_number,
        "episode": content.episode_number,
    }


@app.delete("/api/v1/library/{item_id}")
def delete_library_item(item_id: int, db: Session = Depends(get_db)):
    from content_platform.server.vector_store import vector_store
    item = db.query(ContentSegment).filter(ContentSegment.segment_id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Library item not found")
    
    content_id = item.content_id
    # Remove from Qdrant
    vector_store.delete_reference(content_id)
    
    db.delete(item)
    db.commit()

    # Cleanup Content if no segments left
    has_segments = db.query(ContentSegment).filter(ContentSegment.content_id == content_id).first()
    if not has_segments:
        content = db.query(Content).filter(Content.content_id == content_id).first()
        if content:
            db.delete(content)
            db.commit()

    return {"status": "deleted"}


@app.get("/api/v1/platforms")
def list_platforms(db_platform: Session = Depends(get_platform_db)):
    from content_platform.server.models import PlatformReference
    items = db_platform.query(PlatformReference).all()
    results = []
    for item in items:
        try:
            visual_fp = json.loads(item.visual_fp)[:4] if item.visual_fp else []
        except Exception:
            visual_fp = []
        try:
            logo_fp = json.loads(item.logo_fp)[:4] if item.logo_fp else []
        except Exception:
            logo_fp = []
            
        results.append({
            "id": item.id,
            "platform_id": item.platform_id,
            "platform_name": item.platform_name,
            "platform_type": item.platform_type,
            "visual_fp": visual_fp,
            "logo_fp": logo_fp,
            "ocr_keywords": item.ocr_keywords
        })
    return results


@app.delete("/api/v1/platforms")
def delete_platforms_batch(platform_id: str = None, db_platform: Session = Depends(get_platform_db)):
    from content_platform.server.models import PlatformReference
    if not platform_id:
        raise HTTPException(status_code=400, detail="platform_id query parameter is required")
        
    items = db_platform.query(PlatformReference).filter(PlatformReference.platform_id == platform_id).all()
    if not items:
        return {"status": "success", "message": f"No templates found for platform {platform_id}"}
        
    for item in items:
        db_platform.delete(item)
    db_platform.commit()
    
    # Rebuild platform FAISS index to reflect change once
    initialize_platform_faiss_index(db_platform)
    
    return {"status": "deleted", "count": len(items)}


@app.delete("/api/v1/platforms/{item_id}")
def delete_platform(item_id: int, db_platform: Session = Depends(get_platform_db)):
    from content_platform.server.models import PlatformReference
    item = db_platform.query(PlatformReference).filter(PlatformReference.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Platform reference not found")
    
    db_platform.delete(item)
    db_platform.commit()
    
    # Rebuild platform FAISS index to reflect change
    initialize_platform_faiss_index(db_platform)
    
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
    from content_platform.server.matching import parse_title_metadata
    plat_val, chan_val, series_val, season_val, episode_val = parse_title_metadata(payload.title, payload.category)
    
    content = Content(
        content_id=external_content_id,
        title=payload.title,
        content_type=payload.category,
        platform_id=payload.channel_name or plat_val or chan_val,
        series_name=series_val,
        season_number=season_val,
        episode_number=episode_val
    )
    db.add(content)
    db.flush()

    library_item = ContentSegment(
        content_id=external_content_id,
        segment_index=0,
        segment_offset=0,
        visual_fp=visual_fp_str,
        audio_fp=capture.audio_fp
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
        capture.result.content_id = external_content_id
        capture.result.content_name = payload.title
        capture.result.content_type = payload.category
        capture.result.confidence = 1.0
        capture.result.visual_score = 1.0
        capture.result.audio_score = 1.0
        capture.result.ocr_score = 1.0
        capture.result.logo_score = 1.0
        capture.result.matched_platform = plat_val or capture.result.matched_platform
        capture.result.matched_channel = payload.channel_name or chan_val
        capture.result.series = series_val
        capture.result.season = season_val
        capture.result.episode = episode_val
    else:
        result_record = RecognitionResultRecord(
            capture_id=capture.id,
            content_id=external_content_id,
            content_name=payload.title,
            content_type=payload.category,
            confidence=1.0,
            visual_score=1.0,
            audio_score=1.0,
            ocr_score=1.0,
            logo_score=1.0,
            matched_platform=plat_val or "unknown",
            matched_channel=payload.channel_name or chan_val,
            series=series_val,
            season=season_val,
            episode=episode_val
        )
        db.add(result_record)
        
    db.commit()
    
    # Rebuild playback sessions for the device
    try:
        from content_platform.server.temporal import rebuild_playback_sessions_for_device
        rebuild_playback_sessions_for_device(db, capture.device.device_id)
    except Exception as se:
        logger.error("Failed to rebuild playback sessions for device %s after resolve: %s", capture.device.device_id, se)
        
    # Broadcast the update event to all active SSE clients
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(manager.broadcast({"type": "update"}), main_loop)
        
    return {"status": "resolved", "library_id": library_item.segment_id}


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
def export_sessions_csv(
    db: Session = Depends(get_db),
    start_date: str | None = None,
    end_date: str | None = None
):
    query = db.query(PlaybackSession)
    
    if start_date:
        try:
            clean_start = start_date[:-1] if start_date.endswith("Z") else start_date
            if "T" in clean_start:
                start_dt = datetime.fromisoformat(clean_start)
            else:
                start_dt = datetime.strptime(clean_start, "%Y-%m-%d")
            if start_dt.tzinfo is not None:
                start_dt = start_dt.replace(tzinfo=None)
            query = query.filter(PlaybackSession.start_time >= start_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD or ISO format.")
            
    if end_date:
        try:
            clean_end = end_date[:-1] if end_date.endswith("Z") else end_date
            if "T" in clean_end:
                end_dt = datetime.fromisoformat(clean_end)
            else:
                end_dt = datetime.strptime(clean_end, "%Y-%m-%d").replace(hour=23, minute=59, second=59, microsecond=999999)
            if end_dt.tzinfo is not None:
                end_dt = end_dt.replace(tzinfo=None)
            query = query.filter(PlaybackSession.start_time <= end_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD or ISO format.")

    # Default to last 7 days for sessions to prevent OOM
    if not start_date and not end_date:
        week_ago = datetime.utcnow() - timedelta(days=7)
        query = query.filter(PlaybackSession.start_time >= week_ago)
        
    sessions = query.order_by(PlaybackSession.start_time.desc()).all()
    
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
def export_captures_csv(
    db: Session = Depends(get_db),
    start_date: str | None = None,
    end_date: str | None = None
):
    query = db.query(Capture).outerjoin(RecognitionResultRecord, Capture.id == RecognitionResultRecord.capture_id)
    
    if start_date:
        try:
            clean_start = start_date[:-1] if start_date.endswith("Z") else start_date
            if "T" in clean_start:
                start_dt = datetime.fromisoformat(clean_start)
            else:
                start_dt = datetime.strptime(clean_start, "%Y-%m-%d")
            if start_dt.tzinfo is not None:
                start_dt = start_dt.replace(tzinfo=None)
            query = query.filter(Capture.captured_at >= start_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid start_date format. Use YYYY-MM-DD or ISO format.")
            
    if end_date:
        try:
            clean_end = end_date[:-1] if end_date.endswith("Z") else end_date
            if "T" in clean_end:
                end_dt = datetime.fromisoformat(clean_end)
            else:
                end_dt = datetime.strptime(clean_end, "%Y-%m-%d").replace(hour=23, minute=59, second=59, microsecond=999999)
            if end_dt.tzinfo is not None:
                end_dt = end_dt.replace(tzinfo=None)
            query = query.filter(Capture.captured_at <= end_dt)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid end_date format. Use YYYY-MM-DD or ISO format.")

    # Default to last 24 hours of captures to prevent OOM
    if not start_date and not end_date:
        day_ago = datetime.utcnow() - timedelta(days=1)
        query = query.filter(Capture.captured_at >= day_ago)
        
    captures = query.order_by(Capture.captured_at.desc()).all()
    
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


# ----------------------------------------------------------------------
# SERVER-SIDE VIDEO INGESTION API
# ----------------------------------------------------------------------
import threading
import time
import re
import cv2
import numpy as np

class IngestScanPayload(BaseModel):
    path: str

class IngestStartPayload(BaseModel):
    path: str
    content_type: str  # "series" or "movie"
    title: str
    season: int | None = 1
    platform: str | None = "unknown"
    intro_duration: int | None = 0
    language: str | None = None
    genre: str | None = None

ingest_lock = threading.Lock()
ingest_state = {
    "status": "idle",  # idle, scanning, ingesting, completed, failed
    "message": "",
    "current_file": "",
    "current_index": 0,
    "total_files": 0,
    "percent": 0.0,
    "time_elapsed": 0.0,
    "time_remaining": 0.0,
    "start_time": 0.0
}

def load_audio_via_ffmpeg(video_path: Path, duration: float = None) -> tuple[np.ndarray, int]:
    """Helper to extract audio to a temporary WAV and load it with librosa."""
    import tempfile
    import subprocess
    import os
    import librosa
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
        tmp_path = tmp_wav.name
    try:
        ffmpeg_cmd = ["ffmpeg", "-y", "-i", str(video_path)]
        if duration:
            ffmpeg_cmd += ["-t", str(duration)]
        ffmpeg_cmd += ["-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", tmp_path]
        subprocess.run(ffmpeg_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        audio, sr = librosa.load(tmp_path, sr=16000, mono=True)
        return audio, sr
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

def bg_ingest_worker(
    path: str,
    content_type: str,
    title: str,
    season: int | None,
    platform: str | None,
    intro_duration: int | None,
    language: str | None,
    genre: str | None
):
    global ingest_state
    
    from content_platform.server.db import SessionLocal
    from content_platform.server.models import Content, ContentSegment
    from content_platform.fingerprint.unified import UnifiedFingerprinter
    from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor
    from content_platform.server.faiss_index import FAISSIndex
    from content_platform.server.matching import force_reload_faiss_indexes
    
    FAISS_INDEX_DIR = Path("runtime/faiss")
    VISUAL_INDEX_PATH = FAISS_INDEX_DIR / "visual.index"
    VISUAL_META_PATH = FAISS_INDEX_DIR / "visual_meta.json"
    AUDIO_INDEX_PATH = FAISS_INDEX_DIR / "audio.index"
    AUDIO_META_PATH = FAISS_INDEX_DIR / "audio_meta.json"
    FAISS_VISUAL_DIM = 960
    FAISS_AUDIO_DIM = 130
    SEGMENT_SECONDS = 10
    
    try:
        input_path = Path(path)
        video_extensions = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm")
        video_files = []
        if input_path.is_file():
            if input_path.suffix.lower() in video_extensions:
                video_files.append(input_path)
        else:
            for file in sorted(input_path.rglob("*")):
                if file.is_file() and file.suffix.lower() in video_extensions:
                    video_files.append(file)
                    
        # Sort video files by episode number
        def get_episode_num(name):
            match = re.search(r"(?:episode|ep|ep\.)\s*(\d+)", name, re.IGNORECASE)
            return int(match.group(1)) if match else 0
        video_files = sorted(video_files, key=lambda x: get_episode_num(x.name))
        
        total_files = len(video_files)
        if total_files == 0:
            with ingest_lock:
                ingest_state["status"] = "failed"
                ingest_state["message"] = "No valid video files found at path."
            return
            
        with ingest_lock:
            ingest_state["status"] = "ingesting"
            ingest_state["total_files"] = total_files
            ingest_state["current_index"] = 0
            ingest_state["start_time"] = time.time()
            
        # Load existing FAISS indexes
        if VISUAL_INDEX_PATH.exists() and AUDIO_INDEX_PATH.exists():
            visual_idx = FAISSIndex.load(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH), dim=FAISS_VISUAL_DIM, gpu=True)
            audio_idx = FAISSIndex.load(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH), dim=FAISS_AUDIO_DIM, gpu=True)
        else:
            visual_idx = FAISSIndex(dim=FAISS_VISUAL_DIM, gpu=True)
            audio_idx = FAISSIndex(dim=FAISS_AUDIO_DIM, gpu=True)
            
        db = SessionLocal()
        
        # 1. Ingest Intro once if series and intro_duration > 0
        if content_type == "series" and intro_duration and intro_duration > 0:
            intro_content_id = f"series-{title.lower().replace(' ', '_')}-intro"
            intro_title = f"{title} - Intro"
            existing_intro = db.query(Content).filter(Content.content_id == intro_content_id).first()
            if not existing_intro:
                with ingest_lock:
                    ingest_state["message"] = "Ingesting global intro..."
                db_intro = Content(
                    content_id=intro_content_id,
                    platform_id="global",
                    title=intro_title,
                    content_type="series",
                    series_name=title,
                    season_number=None,
                    episode_number=None
                )
                db.add(db_intro)
                db.flush()
                
                # Extract segment for intro
                first_video = video_files[0]
                cap = cv2.VideoCapture(str(first_video))
                audio, sr = load_audio_via_ffmpeg(first_video, duration=float(intro_duration))
                
                for sec in range(0, intro_duration, SEGMENT_SECONDS):
                    segment_index = sec // SEGMENT_SECONDS
                    frame_number = int(sec * cap.get(cv2.CAP_PROP_FPS))
                    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                    ret, frame = cap.read()
                    if not ret:
                        continue
                        
                    visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
                    if len(visual_fp) != FAISS_VISUAL_DIM:
                        continue
                        
                    start_audio = sec * sr
                    end_audio = min(len(audio), (sec + SEGMENT_SECONDS) * sr)
                    audio_chunk = audio[start_audio:end_audio]
                    if len(audio_chunk) < 100:
                        audio_chunk = np.zeros(sr * SEGMENT_SECONDS, dtype=np.float32)
                        
                    audio_fp = UnifiedFingerprinter.audio_fingerprint(audio_chunk, sr)
                    if len(audio_fp) != FAISS_AUDIO_DIM:
                        continue
                        
                    db_item = ContentSegment(
                        content_id=intro_content_id,
                        segment_index=segment_index,
                        segment_offset=sec,
                        visual_fp=json.dumps(visual_fp),
                        audio_fp=json.dumps(audio_fp)
                    )
                    db.add(db_item)
                    db.flush()
                    
                    meta = {
                        "content_id": intro_content_id,
                        "segment_index": segment_index,
                        "segment_offset": sec
                    }
                    visual_idx.add(np.array(visual_fp, dtype=np.float32).reshape(1, -1), [meta])
                    audio_idx.add(np.array(audio_fp, dtype=np.float32).reshape(1, -1), [meta])
                cap.release()
                db.commit()
                
        # 2. Ingest episodes/videos
        for idx, video_file in enumerate(video_files):
            with ingest_lock:
                ingest_state["current_file"] = video_file.name
                ingest_state["current_index"] = idx + 1
                ingest_state["percent"] = round((idx / total_files) * 100, 1)
                elapsed = time.time() - ingest_state["start_time"]
                ingest_state["time_elapsed"] = round(elapsed, 1)
                if idx > 0:
                    per_file = elapsed / idx
                    ingest_state["time_remaining"] = round(per_file * (total_files - idx), 1)
                else:
                    ingest_state["time_remaining"] = round(25.0 * (total_files - idx), 1)
                    
            if content_type == "series":
                ep_num = get_episode_num(video_file.name)
                if ep_num == 0:
                    ep_num = idx + 1
                content_id = f"series-{title.lower().replace(' ', '_')}-s{season:02d}-e{ep_num:02d}"
                display_title = f"{title} S{season:02d}E{ep_num:02d}"
            else:
                content_id = f"movie-{title.lower().replace(' ', '_')}"
                display_title = title
                
            existing_content = db.query(Content).filter(Content.content_id == content_id).first()
            if not existing_content:
                db_content = Content(
                    content_id=content_id,
                    platform_id=platform or "unknown",
                    title=display_title,
                    content_type=content_type,
                    series_name=title if content_type == "series" else None,
                    season_number=season if content_type == "series" else None,
                    episode_number=ep_num if content_type == "series" else None,
                    language=language,
                    genre=genre
                )
                db.add(db_content)
                db.flush()
                
            cap = cv2.VideoCapture(str(video_file))
            if not cap.isOpened():
                continue
                
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            duration = int(frame_count / fps)
            
            audio, sr = load_audio_via_ffmpeg(video_file)
            
            start_sec = intro_duration if (content_type == "series" and intro_duration) else 0
            for sec in range(start_sec, duration, SEGMENT_SECONDS):
                segment_index = (sec - start_sec) // SEGMENT_SECONDS
                
                existing_seg = db.query(ContentSegment).filter(
                    ContentSegment.content_id == content_id,
                    ContentSegment.segment_index == segment_index
                ).first()
                if existing_seg:
                    continue
                    
                frame_number = int(sec * fps)
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)
                ret, frame = cap.read()
                if not ret:
                    continue
                    
                visual_fp = DeepEmbeddingsExtractor.extract_visual(frame)
                if len(visual_fp) != FAISS_VISUAL_DIM:
                    continue
                    
                start_audio = sec * sr
                end_audio = min(len(audio), (sec + SEGMENT_SECONDS) * sr)
                audio_chunk = audio[start_audio:end_audio]
                if len(audio_chunk) < 100:
                    audio_chunk = np.zeros(sr * SEGMENT_SECONDS, dtype=np.float32)
                    
                audio_fp = UnifiedFingerprinter.audio_fingerprint(audio_chunk, sr)
                if len(audio_fp) != FAISS_AUDIO_DIM:
                    continue
                    
                db_item = ContentSegment(
                    content_id=content_id,
                    segment_index=segment_index,
                    segment_offset=sec,
                    visual_fp=json.dumps(visual_fp),
                    audio_fp=json.dumps(audio_fp)
                )
                db.add(db_item)
                db.flush()
                
                meta = {
                    "content_id": content_id,
                    "segment_index": segment_index,
                    "segment_offset": sec
                }
                visual_idx.add(np.array(visual_fp, dtype=np.float32).reshape(1, -1), [meta])
                audio_idx.add(np.array(audio_fp, dtype=np.float32).reshape(1, -1), [meta])
                
            cap.release()
            db.commit()
            
        # Save updated FAISS indexes
        visual_idx.save(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH))
        audio_idx.save(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH))
        
        # Force reload in-memory FAISS indexes
        force_reload_faiss_indexes()
        db.close()
        
        with ingest_lock:
            ingest_state["status"] = "completed"
            ingest_state["percent"] = 100.0
            ingest_state["time_remaining"] = 0.0
            ingest_state["message"] = "Ingestion completed successfully."
            
    except Exception as ex:
        if db:
            db.rollback()
            db.close()
        with ingest_lock:
            ingest_state["status"] = "failed"
            ingest_state["message"] = f"Error during ingestion: {str(ex)}"

@app.post("/api/v1/ingest/scan")
def scan_ingest_path(payload: IngestScanPayload):
    p = Path(payload.path)
    if not p.exists():
        raise HTTPException(status_code=400, detail=f"Path '{payload.path}' does not exist on the server.")
        
    video_extensions = (".mp4", ".mkv", ".avi", ".mov", ".flv", ".webm")
    video_files = []
    if p.is_file():
        if p.suffix.lower() in video_extensions:
            video_files.append(p)
    else:
        for file in p.rglob("*"):
            if file.is_file() and file.suffix.lower() in video_extensions:
                video_files.append(file)
                
    total_files = len(video_files)
    # Estimate time required (approx 20 seconds per video file as a safe average)
    est_seconds = total_files * 20
    
    return {
        "path": str(p),
        "total_files": total_files,
        "est_seconds": est_seconds
    }

@app.post("/api/v1/ingest/start")
def start_ingest_path(payload: IngestStartPayload):
    global ingest_state
    with ingest_lock:
        if ingest_state["status"] == "ingesting":
            raise HTTPException(status_code=400, detail="Another ingestion process is currently running.")
        ingest_state = {
            "status": "scanning",
            "message": "Initializing background ingestion task...",
            "current_file": "",
            "current_index": 0,
            "total_files": 0,
            "percent": 0.0,
            "time_elapsed": 0.0,
            "time_remaining": 0.0,
            "start_time": 0.0
        }
        
    t = threading.Thread(
        target=bg_ingest_worker,
        args=(
            payload.path,
            payload.content_type,
            payload.title,
            payload.season,
            payload.platform,
            payload.intro_duration,
            payload.language,
            payload.genre
        ),
        daemon=True
    )
    t.start()
    return {"status": "success", "message": "Background ingestion process started successfully"}

@app.get("/api/v1/ingest/status")
def get_ingest_status():
    global ingest_state
    with ingest_lock:
        return ingest_state

@app.get("/api/v1/ingest/series-suggest")
def get_series_suggestions(db: Session = Depends(get_db)):
    # Query distinct series names from registered content
    results = db.query(Content.series_name, Content.content_type, Content.platform_id).filter(Content.series_name != None).distinct().all()
    suggestions = []
    seen = set()
    for row in results:
        if row.series_name and row.series_name not in seen:
            seen.add(row.series_name)
            suggestions.append({
                "series_name": row.series_name,
                "content_type": row.content_type,
                "platform_id": row.platform_id
            })
    return suggestions


@app.post("/api/v1/matching/reload")
def reload_matching_indexes():
    from content_platform.server.matching import force_reload_faiss_indexes
    force_reload_faiss_indexes()
    return {"status": "success", "message": "FAISS indexes hot-reload scheduled"}


@app.get("/api/v1/events")
async def sse_endpoint(request: Request):
    q = await manager.subscribe()
    
    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(q.get(), timeout=1.0)
                    yield f"data: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            manager.unsubscribe(q)
            
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
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
