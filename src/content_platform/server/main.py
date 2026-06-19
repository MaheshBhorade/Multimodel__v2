import asyncio
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
                    from content_platform.shared.models import RecognitionResult, MatchBreakdown
                    result = RecognitionResult(
                        content_name="Unknown Content",
                        content_type="unknown",
                        confidence=0.0,
                        breakdown=MatchBreakdown(
                            visual_score=0.0,
                            audio_score=0.0,
                            ocr_score=0.0,
                            logo_score=0.0
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
