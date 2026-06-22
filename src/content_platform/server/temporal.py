import logging
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

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
            .options(joinedload(RecognitionResultRecord.capture))
            .filter(Device.device_id == device_id)
            .order_by(Capture.captured_at.desc())
            .limit(120)
            .all()
        )
        records.reverse()
        
        if len(records) < 3:
            return

        modified = False

        # Pass 1: Intro Gap-Fill
        # Find all intro indices
        intro_indices = []
        for idx, r in enumerate(records):
            is_intro = False
            if r.content_name:
                name_lower = r.content_name.lower()
                id_lower = (r.content_id or "").lower()
                if "intro" in name_lower or "intro" in id_lower:
                    is_intro = True
            if is_intro:
                intro_indices.append(idx)

        # Connect intro events if they are separated by unknown content and <= 90 seconds
        k = 0
        while k < len(intro_indices) - 1:
            i = intro_indices[k]
            j = intro_indices[k+1]
            
            time_diff = (records[j].capture.captured_at - records[i].capture.captured_at).total_seconds()
            if time_diff <= 90.0:
                all_between_unknown = True
                for idx in range(i + 1, j):
                    r_between = records[idx]
                    is_unknown = r_between.content_name in ("Matching in progress...", "Unknown Content", "unknown", "") or r_between.content_type == "unknown"
                    is_between_intro = False
                    if r_between.content_name:
                        nb_lower = r_between.content_name.lower()
                        ib_lower = (r_between.content_id or "").lower()
                        if "intro" in nb_lower or "intro" in ib_lower:
                            is_between_intro = True
                    if not (is_unknown or is_between_intro):
                        all_between_unknown = False
                        break
                
                if all_between_unknown:
                    for idx in range(i + 1, j):
                        r_between = records[idx]
                        is_unknown = r_between.content_name in ("Matching in progress...", "Unknown Content", "unknown", "") or r_between.content_type == "unknown"
                        if is_unknown:
                            r_between.content_name = records[i].content_name
                            r_between.content_type = records[i].content_type
                            r_between.content_id = records[i].content_id
                            r_between.series = records[i].series
                            r_between.season = records[i].season
                            r_between.episode = records[i].episode
                            r_between.confidence = max(r_between.confidence, records[i].confidence, 0.85)
                            r_between.visual_score = max(r_between.visual_score, records[i].visual_score)
                            r_between.audio_score = max(r_between.audio_score, records[i].audio_score)
                            r_between.matched_platform = records[i].matched_platform
                            modified = True
                            logger.info(
                                "Intro gap-fill (capture_id=%d): filled as '%s'",
                                r_between.capture_id,
                                records[i].content_name
                            )
            k += 1

        # Pass 1.5: Unified Gap-Fill (for Unknowns and transient False Matches)
        # Identify any sequence of captures surrounded by the same known content A
        # where the sequence has content != A, and fill them if the time gap is <= 15 seconds.
        n_records = len(records)
        idx = 1
        MAX_GAP_SECONDS = 15.0
        
        while idx < n_records - 1:
            r = records[idx]
            # Find the preceding known content
            left_idx = idx - 1
            while left_idx >= 0:
                r_left = records[left_idx]
                is_left_unknown = r_left.content_name in ("Matching in progress...", "Unknown Content", "unknown", "") or r_left.content_type == "unknown"
                if not is_left_unknown:
                    break
                left_idx -= 1
                
            if left_idx < 0:
                idx += 1
                continue
                
            left_content_name = records[left_idx].content_name
            
            # Find where the run of "not left_content_name" ends
            right_idx = idx
            while right_idx < n_records:
                r_right = records[right_idx]
                if r_right.content_name == left_content_name:
                    break
                right_idx += 1
                
            if right_idx >= n_records:
                idx += 1
                continue
                
            gap_duration = (records[right_idx].capture.captured_at - records[left_idx].capture.captured_at).total_seconds()
            
            if gap_duration <= MAX_GAP_SECONDS and (right_idx - left_idx) > 1:
                rec_before = records[left_idx]
                for fill_idx in range(left_idx + 1, right_idx):
                    r_fill = records[fill_idx]
                    
                    logger.info(
                        "Unified temporal smoothing gap-fill (capture_id=%d): '%s' -> '%s' (gap duration: %.1fs)",
                        r_fill.capture_id,
                        r_fill.content_name,
                        left_content_name,
                        gap_duration
                    )
                    
                    r_fill.content_name = rec_before.content_name
                    r_fill.content_type = rec_before.content_type
                    r_fill.content_id = rec_before.content_id
                    r_fill.series = rec_before.series
                    r_fill.season = rec_before.season
                    r_fill.episode = rec_before.episode
                    
                    r_fill.confidence = max(r_fill.confidence, rec_before.confidence, 0.85)
                    r_fill.visual_score = max(r_fill.visual_score, rec_before.visual_score)
                    r_fill.audio_score = max(r_fill.audio_score, rec_before.audio_score)
                    r_fill.ocr_score = max(r_fill.ocr_score, rec_before.ocr_score)
                    r_fill.logo_score = max(r_fill.logo_score, rec_before.logo_score)
                    
                    if rec_before.matched_platform and rec_before.matched_platform != "unknown":
                        r_fill.matched_platform = rec_before.matched_platform
                    if rec_before.matched_channel and rec_before.matched_channel != "unknown":
                        r_fill.matched_channel = rec_before.matched_channel
                        
                    modified = True
                
                idx = right_idx
            else:
                idx += 1


        # Pass 2: Transient/Flickering Smoothing
        if len(records) >= window_size:
            half = window_size // 2
            for i in range(half, len(records) - half):
                center = records[i]
                window = records[i - half : i + half + 1]
                
                counts = {}
                content_details = {}
                for r in window:
                    name = r.content_name
                    is_unknown = name in ("Matching in progress...", "Unknown Content", "unknown", "") or r.content_type == "unknown"
                    if is_unknown:
                        name = "Unknown Content"
                    
                    counts[name] = counts.get(name, 0) + 1
                    if name != "Unknown Content":
                        if name not in content_details:
                            content_details[name] = {
                                "type": r.content_type,
                                "content_id": r.content_id,
                                "series": r.series,
                                "season": r.season,
                                "episode": r.episode,
                                "confidences": [],
                                "vis_scores": [],
                                "aud_scores": [],
                                "ocr_scores": [],
                                "logo_scores": [],
                                "platforms": []
                            }
                        else:
                            if content_details[name]["content_id"] is None and r.content_id is not None:
                                content_details[name]["content_id"] = r.content_id
                            if content_details[name]["series"] is None and r.series is not None:
                                content_details[name]["series"] = r.series
                            if content_details[name]["season"] is None and r.season is not None:
                                content_details[name]["season"] = r.season
                            if content_details[name]["episode"] is None and r.episode is not None:
                                content_details[name]["episode"] = r.episode
                                
                        content_details[name]["confidences"].append(r.confidence)
                        content_details[name]["vis_scores"].append(r.visual_score)
                        content_details[name]["aud_scores"].append(r.audio_score)
                        content_details[name]["ocr_scores"].append(r.ocr_score)
                        content_details[name]["logo_scores"].append(r.logo_score)
                        if r.matched_platform and r.matched_platform != "unknown":
                            content_details[name]["platforms"].append(r.matched_platform)

                if not counts:
                    continue

                dominant_name = max(counts, key=counts.get)
                dominant_count = counts[dominant_name]

                majority_threshold = (window_size // 2) + 1
                if dominant_count >= majority_threshold:
                    if center.content_name != dominant_name:
                        if dominant_name == "Unknown Content":
                            logger.info(
                                "Smoothing transient match to Unknown (capture_id=%d): '%s' -> 'Unknown Content'",
                                center.capture_id,
                                center.content_name
                            )
                            center.content_name = "Unknown Content"
                            center.content_type = "unknown"
                            center.content_id = None
                            center.series = None
                            center.season = None
                            center.episode = None
                            center.matched_platform = "unknown"
                            modified = True
                        else:
                            details = content_details[dominant_name]
                            avg_conf = sum(details["confidences"]) / len(details["confidences"])
                            avg_vis = sum(details["vis_scores"]) / len(details["vis_scores"])
                            avg_aud = sum(details["aud_scores"]) / len(details["aud_scores"])
                            avg_ocr = sum(details["ocr_scores"]) / len(details["ocr_scores"])
                            avg_logo = sum(details["logo_scores"]) / len(details["logo_scores"])
                            
                            boosted_conf = max(center.confidence, avg_conf, 0.85)
                            
                            logger.info(
                                "Smoothing transient match (capture_id=%d): '%s' -> '%s' (confidence boosted to %.2f)",
                                center.capture_id,
                                center.content_name,
                                dominant_name,
                                boosted_conf
                            )
                            
                            center.content_name = dominant_name
                            center.content_type = details["type"]
                            center.content_id = details["content_id"]
                            center.series = details["series"]
                            center.season = details["season"]
                            center.episode = details["episode"]
                            
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

        # Pass 3: Consecutive Confirmation Filter
        # Revert any run of known content that is shorter than MIN_CONSECUTIVE to "Unknown Content",
        # unless it is at the very end of the records list (active run).
        MIN_CONSECUTIVE = 3
        n = len(records)
        i = 0
        while i < n:
            r = records[i]
            is_unknown = r.content_name in ("Matching in progress...", "Unknown Content", "unknown", "") or r.content_type == "unknown"
            if is_unknown:
                i += 1
                continue
            
            # Start of a run of known content
            run_name = r.content_name
            run_indices = [i]
            j = i + 1
            while j < n:
                r_next = records[j]
                is_next_unknown = r_next.content_name in ("Matching in progress...", "Unknown Content", "unknown", "") or r_next.content_type == "unknown"
                if is_next_unknown or r_next.content_name != run_name:
                    break
                run_indices.append(j)
                j += 1
            
            # Revert if shorter than threshold and has ended (not extending to the latest capture n-1)
            if len(run_indices) < MIN_CONSECUTIVE and run_indices[-1] < n - 1:
                for idx in run_indices:
                    r_revert = records[idx]
                    logger.info(
                        "Reverting short run of '%s' (length %d < %d) at capture_id=%d to Unknown Content",
                        run_name,
                        len(run_indices),
                        MIN_CONSECUTIVE,
                        r_revert.capture_id
                    )
                    r_revert.content_name = "Unknown Content"
                    r_revert.content_type = "unknown"
                    r_revert.content_id = None
                    r_revert.series = None
                    r_revert.season = None
                    r_revert.episode = None
                    r_revert.matched_platform = "unknown"
                    modified = True
            
            i = j

        if modified:
            db.commit()
    except Exception as e:
        logger.exception("Failed to smooth recognition results: %s", e)


