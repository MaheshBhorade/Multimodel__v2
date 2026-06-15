import logging
from sqlalchemy import select
from sqlalchemy.orm import Session

from content_platform.server.models import Capture, RecognitionResultRecord, Device, PlaybackSession

logger = logging.getLogger(__name__)


def rebuild_playback_sessions_for_device(db: Session, device_id: str) -> None:
    """
    Rebuilds playback sessions for a given device by running a temporal state machine
    over all chronologically ordered recognition results.
    
    Logic:
      - Start threshold: 12 consecutive entries of the same known content to start playing.
      - Switch threshold: 10 consecutive entries of a different content (including 'unknown')
        to switch playing content (or return to idle).
    """
    try:
        # Fetch captures and recognition result records for the device, sorted chronologically
        records = (
            db.query(
                Capture.captured_at,
                RecognitionResultRecord.content_name,
                RecognitionResultRecord.content_type
            )
            .join(RecognitionResultRecord, Capture.id == RecognitionResultRecord.capture_id)
            .join(Device, Device.id == Capture.device_id)
            .filter(Device.device_id == device_id)
            .order_by(Capture.captured_at.asc())
            .all()
        )
        
        sessions = []
        
        START_THRESHOLD = 12
        SWITCH_THRESHOLD = 10
        
        # State machine variables
        active_content = None
        active_content_type = None
        active_start_time = None
        active_end_time = None
        active_entries = []
        
        candidate_content = None
        candidate_content_type = None
        candidate_entries = []
        
        for captured_at, content_name, content_type in records:
            
            # Treat "Matching in progress...", empty, or "unknown" as unrecognized
            is_unknown = content_name in ("Matching in progress...", "Unknown Content", "unknown", "") or content_type == "unknown"
            
            if active_content is None:
                # We are currently IDLE (not playing any content)
                if is_unknown:
                    # Reset candidate
                    candidate_content = None
                    candidate_content_type = None
                    candidate_entries = []
                else:
                    if content_name == candidate_content:
                        candidate_entries.append(captured_at)
                        if len(candidate_entries) >= START_THRESHOLD:
                            # Start threshold reached! Transition to ACTIVE
                            active_content = candidate_content
                            active_content_type = candidate_content_type
                            active_start_time = candidate_entries[0]
                            active_end_time = captured_at
                            active_entries = list(candidate_entries)
                            
                            # Reset candidate
                            candidate_content = None
                            candidate_content_type = None
                            candidate_entries = []
                    else:
                        candidate_content = content_name
                        candidate_content_type = content_type
                        candidate_entries = [captured_at]
            else:
                # We are ACTIVE (playing active_content)
                if content_name == active_content:
                    # Continuation of current content
                    active_end_time = captured_at
                    active_entries.append(captured_at)
                    
                    # Reset switch candidate
                    candidate_content = None
                    candidate_content_type = None
                    candidate_entries = []
                else:
                    # Candidate for switching content (or transitioning back to IDLE via 'unknown')
                    if content_name == candidate_content:
                        candidate_entries.append(captured_at)
                        if len(candidate_entries) >= SWITCH_THRESHOLD:
                            # Switch threshold reached! Complete active session
                            sessions.append({
                                "device_id": device_id,
                                "content_name": active_content,
                                "content_type": active_content_type,
                                "start_time": active_start_time,
                                "end_time": active_end_time,
                                "entry_count": len(active_entries),
                            })
                            
                            if is_unknown:
                                # Transition back to IDLE
                                active_content = None
                                active_content_type = None
                                active_start_time = None
                                active_end_time = None
                                active_entries = []
                            else:
                                # Transition to ACTIVE for the new content
                                active_content = candidate_content
                                active_content_type = candidate_content_type
                                active_start_time = candidate_entries[0]
                                active_end_time = captured_at
                                active_entries = list(candidate_entries)
                                
                            # Reset candidate
                            candidate_content = None
                            candidate_content_type = None
                            candidate_entries = []
                    else:
                        candidate_content = content_name
                        candidate_content_type = content_type
                        candidate_entries = [captured_at]
                        
        # Save final active session if exists
        if active_content is not None:
            sessions.append({
                "device_id": device_id,
                "content_name": active_content,
                "content_type": active_content_type,
                "start_time": active_start_time,
                "end_time": active_end_time,
                "entry_count": len(active_entries),
            })
            
        # Delete old sessions for this device using ORM to keep identity map in sync
        old_sessions = db.query(PlaybackSession).filter(PlaybackSession.device_id == device_id).all()
        for oses in old_sessions:
            db.delete(oses)
        
        # Insert new sessions
        for s in sessions:
            duration = (s["end_time"] - s["start_time"]).total_seconds()
            db_sess = PlaybackSession(
                device_id=s["device_id"],
                content_name=s["content_name"],
                content_type=s["content_type"],
                start_time=s["start_time"],
                end_time=s["end_time"],
                duration_seconds=duration,
                entry_count=s["entry_count"],
            )
            db.add(db_sess)
            
        db.commit()
        logger.info("Rebuilt playback sessions for device %s. Found %d sessions.", device_id, len(sessions))
    except Exception as e:
        db.rollback()
        logger.exception("Failed to rebuild playback sessions for device %s: %s", device_id, e)


def smooth_recent_captures_for_device(db: Session, device_id: str, window_size: int = 5) -> None:
    """
    Smooths out transient false/flickering recognition results for a device.
    For each capture, it looks at a local window of size `window_size` (e.g., 5).
    If a single known content dominates the window, any different/transient
    content in the center of the window is 'rounded up' to the dominant content,
    and its confidence is boosted.
    """
    try:
        # Fetch last 40 recognition records chronologically ascending
        records = (
            db.query(RecognitionResultRecord)
            .join(Capture, Capture.id == RecognitionResultRecord.capture_id)
            .join(Device, Device.id == Capture.device_id)
            .filter(Device.device_id == device_id)
            .order_by(Capture.captured_at.desc())
            .limit(40)
            .all()
        )
        records.reverse()
        
        if len(records) < window_size:
            return

        half = window_size // 2
        modified = False

        for i in range(half, len(records) - half):
            center = records[i]
            window = records[i - half : i + half + 1]
            
            # Count occurrences of each known content in the window
            counts = {}
            content_details = {}
            for r in window:
                name = r.content_name
                is_unknown = name in ("Matching in progress...", "Unknown Content", "unknown", "") or r.content_type == "unknown"
                if is_unknown:
                    continue
                counts[name] = counts.get(name, 0) + 1
                if name not in content_details:
                    content_details[name] = {
                        "type": r.content_type,
                        "confidences": [],
                        "vis_scores": [],
                        "aud_scores": [],
                        "ocr_scores": [],
                        "logo_scores": [],
                        "platforms": []
                    }
                content_details[name]["confidences"].append(r.confidence)
                content_details[name]["vis_scores"].append(r.visual_score)
                content_details[name]["aud_scores"].append(r.audio_score)
                content_details[name]["ocr_scores"].append(r.ocr_score)
                content_details[name]["logo_scores"].append(r.logo_score)
                if r.matched_platform and r.matched_platform != "unknown":
                    content_details[name]["platforms"].append(r.matched_platform)

            if not counts:
                continue

            # Find dominant content name
            dominant_name = max(counts, key=counts.get)
            dominant_count = counts[dominant_name]

            # Require that the dominant content appears in the majority of the window
            majority_threshold = (window_size // 2) + 1
            if dominant_count >= majority_threshold:
                # If center is different from dominant, we smooth it!
                if center.content_name != dominant_name:
                    details = content_details[dominant_name]
                    avg_conf = sum(details["confidences"]) / len(details["confidences"])
                    avg_vis = sum(details["vis_scores"]) / len(details["vis_scores"])
                    avg_aud = sum(details["aud_scores"]) / len(details["aud_scores"])
                    avg_ocr = sum(details["ocr_scores"]) / len(details["ocr_scores"])
                    avg_logo = sum(details["logo_scores"]) / len(details["logo_scores"])
                    
                    # Boost confidence score
                    boosted_conf = max(center.confidence, avg_conf, 0.85)
                    
                    logger.info(
                        "Smoothing transient match at %s: '%s' -> '%s' (confidence boosted to %.2f)",
                        center.capture.captured_at,
                        center.content_name,
                        dominant_name,
                        boosted_conf
                    )
                    
                    center.content_name = dominant_name
                    center.content_type = details["type"]
                    center.confidence = boosted_conf
                    center.visual_score = max(center.visual_score, avg_vis)
                    center.audio_score = max(center.audio_score, avg_aud)
                    center.ocr_score = max(center.ocr_score, avg_ocr)
                    center.logo_score = max(center.logo_score, avg_logo)
                    if details["platforms"]:
                        center.matched_platform = details["platforms"][0]
                    else:
                        center.matched_platform = "unknown"
                    modified = True

        if modified:
            db.commit()
    except Exception as e:
        logger.exception("Failed to smooth recognition results: %s", e)

