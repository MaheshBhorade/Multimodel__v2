import json
from dataclasses import dataclass
import numpy as np
from pathlib import Path
from content_platform.server.faiss_index import FAISSIndex
from content_platform.server.models import Content, ContentSegment, PlatformReference
from content_platform.shared.models import FingerprintPayload, MatchBreakdown, RecognitionResult

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

# FAISS index configuration
FAISS_VISUAL_DIM = 960
FAISS_AUDIO_DIM = 130
FAISS_INDEX_DIR = Path("runtime/faiss")
FAISS_INDEX_DIR.mkdir(parents=True, exist_ok=True)
VISUAL_INDEX_PATH = FAISS_INDEX_DIR / "visual.index"
VISUAL_META_PATH = FAISS_INDEX_DIR / "visual_meta.json"
AUDIO_INDEX_PATH = FAISS_INDEX_DIR / "audio.index"
AUDIO_META_PATH = FAISS_INDEX_DIR / "audio_meta.json"

visual_index = None
audio_index = None

PLATFORM_VISUAL_INDEX_PATH = FAISS_INDEX_DIR / "platform_visual.index"
PLATFORM_VISUAL_META_PATH = FAISS_INDEX_DIR / "platform_visual_meta.json"
platform_visual_index = None

import logging
logger = logging.getLogger(__name__)


def parse_title_metadata(title: str, category: str):
    """
    Parses a title string to extract platform, channel, series, season, and episode.
    e.g., 'Goyamart Episode 94' -> series='Goyamart', episode=94
    """
    import re
    series = None
    season = None
    episode = None
    platform = None
    channel = None
    
    title_lower = title.lower()
    
    if category == "channel":
        channel = title
    elif category == "platform":
        platform = title
        
    se_match = re.search(r's(\d+)\s*e(\d+)', title_lower)
    if se_match:
        season = int(se_match.group(1))
        episode = int(se_match.group(2))
        series = title[:se_match.start()].strip(" -_")
    else:
        ep_match = re.search(r'(?:episode|ep|ep\.)\s*(\d+)', title_lower)
        if ep_match:
            episode = int(ep_match.group(1))
            series = title[:ep_match.start()].strip(" -_")
            
        s_match = re.search(r'(?:season|s)\s*(\d+)', title_lower)
        if s_match:
            season = int(s_match.group(1))
            if not series:
                series = title[:s_match.start()].strip(" -_")
                
    if not series:
        series = title
        
    return platform, channel, series, season, episode


def initialize_platform_faiss_index(db_platform: Session) -> None:
    global platform_visual_index
    db_count = db_platform.query(PlatformReference).count()
    if platform_visual_index is not None:
        if len(platform_visual_index.id_to_content) == db_count and platform_visual_index.dim == FAISS_VISUAL_DIM:
            return
        logger.info(f"Platform FAISS index count/dim mismatch with DB. Rebuilding...")
        platform_visual_index = None

    # Try loading from disk first
    if PLATFORM_VISUAL_INDEX_PATH.exists() and PLATFORM_VISUAL_META_PATH.exists():
        try:
            loaded_index = FAISSIndex.load(str(PLATFORM_VISUAL_INDEX_PATH), str(PLATFORM_VISUAL_META_PATH), dim=FAISS_VISUAL_DIM, gpu=True)
            if len(loaded_index.id_to_content) == db_count:
                platform_visual_index = loaded_index
                logger.info("Platform FAISS index loaded from disk successfully.")
                return
            logger.info(f"Platform FAISS index on disk size ({len(loaded_index.id_to_content)}) mismatch with DB ({db_count}). Rebuilding...")
        except Exception as e:
            logger.warning(f"Failed to load Platform FAISS index from disk: {e}. Rebuilding...")

    # Rebuild from database
    logger.info("Rebuilding Platform FAISS index from database PlatformReference...")
    try:
        vis_idx = FAISSIndex(dim=FAISS_VISUAL_DIM, gpu=True)
        items = db_platform.query(PlatformReference).all()
        logger.info(f"Found {len(items)} platform library items in DB.")
        
        for item in items:
            try:
                vis_fp = json.loads(item.visual_fp)
                if len(vis_fp) == FAISS_VISUAL_DIM:
                    meta = {
                        "platform_id": item.platform_id,
                        "platform_name": item.platform_name,
                        "platform_type": item.platform_type
                    }
                    vis_idx.add(np.array(vis_fp, dtype=np.float32).reshape(1, -1), [meta])
            except Exception as e:
                logger.warning(f"Failed to add platform item {item.id} to FAISS index: {e}")
                
        vis_idx.save(str(PLATFORM_VISUAL_INDEX_PATH), str(PLATFORM_VISUAL_META_PATH))
        logger.info("Platform FAISS index built and saved successfully.")
        platform_visual_index = vis_idx
    except Exception as e:
        logger.error(f"Error rebuilding Platform FAISS index: {e}")


def match_platform(db_platform: Session, payload: FingerprintPayload) -> str:
    initialize_platform_faiss_index(db_platform)
    
    if not payload.visual_fps:
        return "unknown"
        
    global platform_visual_index
    
    # Check blank visual
    first_frame = payload.visual_fps[0] if isinstance(payload.visual_fps[0], list) else payload.visual_fps
    if is_blank_signal(first_frame):
        return "unknown"

    # Use FAISS index if available and compatible
    use_faiss = False
    if platform_visual_index is not None:
        if isinstance(payload.visual_fps[0], (list, tuple, np.ndarray)):
            payload_vis_dim = len(payload.visual_fps[0])
        else:
            payload_vis_dim = len(payload.visual_fps)
            
        if payload_vis_dim == platform_visual_index.dim:
            use_faiss = True

    matches = []
    if use_faiss:
        frames_to_search = payload.visual_fps if isinstance(payload.visual_fps[0], list) else [payload.visual_fps]
        visual_scores = {}
        for frame in frames_to_search:
            frame_arr = np.array(frame, dtype=np.float32)
            visual_results = platform_visual_index.search(frame_arr, k=5)
            for res in visual_results:
                platform_id = res["content_id"]
                sim = res["score"]
                if platform_id not in visual_scores or sim > visual_scores[platform_id]:
                    visual_scores[platform_id] = sim
                
        for platform_id, score in visual_scores.items():
            matches.append({"platform": platform_id, "score": score})
    else:
        # Brute force fallback
        items = db_platform.query(PlatformReference).all()
        if not items:
            return "unknown"
            
        for item in items:
            try:
                lib_visual = json.loads(item.visual_fp)
            except Exception:
                continue
                
            if payload.visual_fps and isinstance(payload.visual_fps[0], (int, float)):
                sim = cosine_like_similarity(payload.visual_fps, lib_visual)
            else:
                similarities = [
                    cosine_like_similarity(fp, lib_visual)
                    for fp in payload.visual_fps
                ]
                sim = max(similarities) if similarities else 0.0
                
            matches.append({"platform": item.platform_id, "score": sim})
            
    if not matches:
        return "unknown"
        
    matches.sort(key=lambda x: x["score"], reverse=True)
    best_match = matches[0]
    
    # Increase threshold to 0.65 to avoid false positives during full-screen video playback
    if best_match["score"] >= 0.65:
        return best_match["platform"]
        
    return "unknown"


def initialize_faiss_indexes(db: Session) -> None:
    global visual_index, audio_index
    db_count = db.query(ContentSegment).count()
    if visual_index is not None and audio_index is not None:
        if len(visual_index.id_to_content) == db_count and visual_index.dim == FAISS_VISUAL_DIM and audio_index.dim == FAISS_AUDIO_DIM:
            return
        logger.info(f"Content FAISS index count/dim mismatch. Rebuilding...")
        visual_index = None
        audio_index = None

    # Try loading from disk first
    if VISUAL_INDEX_PATH.exists() and VISUAL_META_PATH.exists() and AUDIO_INDEX_PATH.exists() and AUDIO_META_PATH.exists():
        try:
            vis_loaded = FAISSIndex.load(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH), dim=FAISS_VISUAL_DIM, gpu=True)
            aud_loaded = FAISSIndex.load(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH), dim=FAISS_AUDIO_DIM, gpu=True)
            if len(vis_loaded.id_to_content) == db_count:
                visual_index = vis_loaded
                audio_index = aud_loaded
                logger.info("FAISS indexes loaded from disk successfully.")
                return
            logger.info(f"Content FAISS index on disk size ({len(vis_loaded.id_to_content)}) mismatch with DB ({db_count}). Rebuilding...")
        except Exception as e:
            logger.warning(f"Failed to load FAISS indexes from disk: {e}. Rebuilding from database...")

    # Rebuild from database
    logger.info("Rebuilding FAISS indexes from database ContentSegment...")
    try:
        vis_idx = FAISSIndex(dim=FAISS_VISUAL_DIM, gpu=True)
        aud_idx = FAISSIndex(dim=FAISS_AUDIO_DIM, gpu=True)
        
        items = db.query(ContentSegment).all()
        logger.info(f"Found {len(items)} library items in DB.")
        
        for item in items:
            try:
                vis_fp = json.loads(item.visual_fp)
                aud_fp = json.loads(item.audio_fp)
                meta = {
                    "content_id": item.content_id,
                    "segment_index": item.segment_index,
                    "segment_offset": item.segment_offset
                }
                if len(vis_fp) == FAISS_VISUAL_DIM:
                    vis_idx.add(np.array(vis_fp, dtype=np.float32).reshape(1, -1), [meta])
                if len(aud_fp) == FAISS_AUDIO_DIM:
                    aud_idx.add(np.array(aud_fp, dtype=np.float32).reshape(1, -1), [meta])
            except Exception as e:
                logger.warning(f"Failed to add item {item.segment_id} to FAISS index: {e}")
                
        vis_idx.save(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH))
        aud_idx.save(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH))
        logger.info("FAISS indexes built and saved successfully.")
        
        visual_index = vis_idx
        audio_index = aud_idx
    except Exception as e:
        logger.error(f"Error rebuilding FAISS indexes: {e}")


from content_platform.server.models import Content, ContentSegment
from content_platform.shared.models import FingerprintPayload, MatchBreakdown, RecognitionResult


@dataclass
class WeightedScores:
    visual: float
    audio: float
    ocr: float
    logo: float

    @property
    def final(self) -> float:
        return (self.visual * 0.70) + (self.audio * 0.30)


def cosine_like_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity for high-dimensional vectors, with DCT-specific DC exclusion if needed."""
    if not left or not right or len(left) != len(right):
        return 0.0
    
    # For legacy 256-dim DCT vectors, exclude index 0 (the DC component) to prevent brightness bias.
    # For deep learning feature embeddings (e.g., 960-dim), use the full vector.
    if len(left) == 256:
        left_arr = np.array(left[1:])
        right_arr = np.array(right[1:])
    elif len(left) > 1:
        left_arr = np.array(left)
        right_arr = np.array(right)
    else:
        left_arr = np.array(left)
        right_arr = np.array(right)
    
    norm_left = np.linalg.norm(left_arr)
    norm_right = np.linalg.norm(right_arr)
    
    if norm_left < 1e-10 or norm_right < 1e-10:
        return 0.0
    
    if np.allclose(left_arr, right_arr):
        return 1.0
        
    cosine_sim = np.dot(left_arr, right_arr) / (norm_left * norm_right)
    val = max(0.0, min(1.0, cosine_sim))
    if abs(val - 1.0) < 1e-9:
        return 1.0
    return float(val)


def audio_similarity(left: list[float] | str, right: list[float] | str) -> float:
    """Cosine similarity for MFCC audio fingerprints supporting float lists and string formats."""
    if not left or not right:
        return 0.0
        
    if isinstance(left, str) or isinstance(right, str):
        if left == right:
            return 1.0
        try:
            left_str = str(left)
            right_str = str(right)
            if left_str.startswith("ae-hash-") and right_str.startswith("ae-hash-"):
                left_vals = [float(x) for x in left_str.replace("ae-hash-", "").split("-")]
                right_vals = [float(x) for x in right_str.replace("ae-hash-", "").split("-")]
                if len(left_vals) == len(right_vals) and len(left_vals) > 0:
                    left_arr = np.array(left_vals)
                    right_arr = np.array(right_vals)
                    norm_left = np.linalg.norm(left_arr)
                    norm_right = np.linalg.norm(right_arr)
                    if norm_left > 0 and norm_right > 0:
                        return max(0.0, min(1.0, np.dot(left_arr, right_arr) / (norm_left * norm_right)))
        except Exception:
            pass
        if isinstance(left, str) and isinstance(right, str):
            return 0.35 if left.split("-")[0] == right.split("-")[0] else 0.0
        return 0.0

    left_arr = np.array(left)
    right_arr = np.array(right)
    norm_left = np.linalg.norm(left_arr)
    norm_right = np.linalg.norm(right_arr)
    if norm_left == 0 or norm_right == 0:
        return 0.0
    cosine_sim = np.dot(left_arr, right_arr) / (norm_left * norm_right)
    val = max(0.0, min(1.0, cosine_sim))
    if abs(val - 1.0) < 1e-9:
        return 1.0
    return float(val)


def ocr_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_tokens = set(left.lower().split())
    right_tokens = set(right.lower().split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def is_blank_signal(visual_fp: list[float]) -> bool:
    if not visual_fp:
        return True
    # All zeros (deep learning blank fallback)
    if all(abs(x) < 1e-10 for x in visual_fp):
        return True
    # A solid color frame (like green screen or black screen) has only the DC component (index 0) non-zero.
    # We check if all AC components (indices 1 to end) are zero or extremely close to it.
    return all(abs(x) < 1e-4 for x in visual_fp[1:])


def _is_audio_unavailable(audio_fp) -> bool:
    """Check if audio fingerprint is missing or all zeros (recording failed)."""
    if audio_fp is None:
        return True
    if isinstance(audio_fp, str):
        return False  # String-based audio is always "available" (legacy format)
    if isinstance(audio_fp, list):
        return all(abs(x) < 1e-10 for x in audio_fp)
    return True


def match_content(db: Session, payload: FingerprintPayload, platform_id: str = "unknown") -> RecognitionResult:
    from content_platform.server.models import Capture, RecognitionResultRecord, Device
    
    # Check if more than 50% of the payload frames are blank/lost video signal (like a green screen)
    is_payload_blank = False
    if payload.visual_fps:
        if isinstance(payload.visual_fps[0], (int, float)):
            is_payload_blank = is_blank_signal(payload.visual_fps)
        else:
            blank_count = sum(1 for fp in payload.visual_fps if is_blank_signal(fp))
            is_payload_blank = (blank_count / len(payload.visual_fps)) > 0.5

    # Check if audio is unavailable (all zeros = recording failed on Pi)
    audio_missing = _is_audio_unavailable(payload.audio_fp)

    # Use FAISS indexes when available and dimensions match; otherwise fallback to brute‑force.
    use_faiss_visual = False
    legacy_payload = False
    if visual_index is not None and not is_payload_blank and payload.visual_fps:
        if isinstance(payload.visual_fps[0], (list, tuple, np.ndarray)):
            payload_vis_dim = len(payload.visual_fps[0])
        else:
            payload_vis_dim = len(payload.visual_fps)
        if payload_vis_dim == visual_index.dim:
            use_faiss_visual = True
        else:
            legacy_payload = True

    use_faiss_audio = False
    if audio_index is not None and not audio_missing and payload.audio_fp is not None and not legacy_payload:
        if isinstance(payload.audio_fp, (list, tuple, np.ndarray)):
            payload_aud_dim = len(payload.audio_fp)
        else:
            payload_aud_dim = 0
        if payload_aud_dim == audio_index.dim:
            use_faiss_audio = True

    use_faiss = (use_faiss_visual or use_faiss_audio) and not legacy_payload

    # Look up the last RecognitionResultRecord for this device
    last_record = None
    device = db.query(Device).filter(Device.device_id == payload.device_id).first()
    if device:
        last_record = (
            db.query(RecognitionResultRecord)
            .join(Capture, Capture.id == RecognitionResultRecord.capture_id)
            .filter(Capture.device_id == device.id)
            .order_by(Capture.captured_at.desc())
            .first()
        )
        
    last_content_id = None
    last_offset = None
    last_captured_at = None
    if last_record and last_record.capture:
        if last_record.capture.result and last_record.capture.result.content_id:
            last_content_id = last_record.capture.result.content_id
            last_offset = last_record.playback_position
            last_captured_at = last_record.capture.captured_at

    visual_scores = {}
    audio_scores = {}

    # Query allowed content IDs for this platform (including global/platform-agnostic content)
    allowed_content_ids = set()
    if platform_id and platform_id != "unknown":
        allowed_content_ids = {
            c.content_id 
            for c in db.query(Content.content_id)
            .filter((Content.platform_id == platform_id) | (Content.platform_id == "global") | (Content.platform_id == None))
            .all()
        }

    if use_faiss:
        if use_faiss_visual:
            # Prepare query vector for visual
            if isinstance(payload.visual_fps[0], list):
                avg_visual = np.mean(np.array(payload.visual_fps, dtype=np.float32), axis=0)
            else:
                avg_visual = np.array(payload.visual_fps, dtype=np.float32)
            # Search visual index (k=10)
            visual_results = visual_index.search(avg_visual, k=10)
            for res in visual_results:
                if allowed_content_ids and res["content_id"] not in allowed_content_ids:
                    continue
                key = (res["content_id"], res["segment_offset"])
                sim = res["score"]
                if key not in visual_scores or sim > visual_scores[key]:
                    visual_scores[key] = sim

        if use_faiss_audio:
            # Prepare query vector for audio
            audio_vec = np.array(payload.audio_fp, dtype=np.float32)
            # Search audio index (k=10)
            audio_results = audio_index.search(audio_vec, k=10)
            for res in audio_results:
                if allowed_content_ids and res["content_id"] not in allowed_content_ids:
                    continue
                key = (res["content_id"], res["segment_offset"])
                sim = res["score"]
                if key not in audio_scores or sim > audio_scores[key]:
                    audio_scores[key] = sim
    else:
        # Fallback to brute‑force method
        if allowed_content_ids:
            segments = db.query(ContentSegment).filter(ContentSegment.content_id.in_(allowed_content_ids)).all()
        else:
            segments = db.query(ContentSegment).all()

        for segment in segments:
            key = (segment.content_id, segment.segment_offset)
            try:
                lib_visual = json.loads(segment.visual_fp)
            except Exception:
                lib_visual = []
            
            try:
                lib_audio = json.loads(segment.audio_fp)
            except Exception:
                lib_audio = segment.audio_fp

            # Visual similarity
            if payload.visual_fps and isinstance(payload.visual_fps[0], (int, float)):
                vis_sim = cosine_like_similarity(payload.visual_fps, lib_visual)
            else:
                similarities = [
                    cosine_like_similarity(fp, lib_visual)
                    for fp in payload.visual_fps
                ]
                vis_sim = max(similarities) if similarities else 0.0

            # Audio similarity
            aud_sim = audio_similarity(payload.audio_fp, lib_audio)
            
            visual_scores[key] = vis_sim
            audio_scores[key] = aud_sim

    # Combine scores per (content_id, segment_offset)
    all_keys = set(visual_scores.keys()) | set(audio_scores.keys())
    matches = []
    for key in all_keys:
        cid, offset = key
        vis_sim = visual_scores.get(key, 0.0)
        aud_sim = audio_scores.get(key, 0.0)
        
        # Adaptive scoring
        if is_payload_blank and not audio_missing:
            combined_score = aud_sim
        elif is_payload_blank and audio_missing:
            combined_score = 0.0
        elif audio_missing:
            combined_score = vis_sim
        else:
            if vis_sim >= 0.75:
                combined_score = vis_sim
            elif vis_sim >= 0.60 and aud_sim >= 0.10:
                combined_score = max(vis_sim, (vis_sim * 0.70) + (aud_sim * 0.30))
            else:
                combined_score = (vis_sim * 0.70) + (aud_sim * 0.30)

        # Apply temporal progression boost
        is_continuous = False
        if last_content_id and cid == last_content_id and last_offset is not None and last_captured_at is not None:
            delta_time = (payload.timestamp - last_captured_at).total_seconds()
            expected_offset = last_offset + delta_time
            if abs(offset - expected_offset) <= 15:
                combined_score = min(1.0, combined_score + 0.15)
                is_continuous = True

        content_obj = db.scalars(select(Content).where(Content.content_id == cid).limit(1)).first()
        if content_obj:
            matches.append({
                "content": content_obj,
                "score": combined_score,
                "visual_score": vis_sim,
                "audio_score": aud_sim,
                "segment_offset": offset,
                "is_continuous": is_continuous
            })

    # Sort matches by score descending
    matches.sort(key=lambda x: x["score"], reverse=True)

    # Take top 10 matches
    top_10 = matches[:10]

    # Group by (content_id, segment_offset)
    votes = {}
    for m in top_10:
        key = (m["content"].content_id, m["segment_offset"])
        if key not in votes:
            votes[key] = []
        votes[key].append(m)

    # Segment-level voting
    winner_key = None
    max_votes = -1
    best_winner_score = -1

    for key, group_matches in votes.items():
        vote_count = len(group_matches)
        group_max_score = max(m["score"] for m in group_matches)
        if group_max_score > best_winner_score or (abs(group_max_score - best_winner_score) < 1e-9 and vote_count > max_votes):
            max_votes = vote_count
            winner_key = key
            best_winner_score = group_max_score

    # Construct final result
    if winner_key is not None:
        winning_group = votes[winner_key]
        best_match = max(winning_group, key=lambda x: x["score"])
        content = best_match["content"]
        confidence = best_match["score"]
        vis_score = best_match["visual_score"]
        aud_score = best_match["audio_score"]
        playback_pos = float(best_match["segment_offset"])

        # Map custom categories to valid Pydantic type Literal
        content_type = content.content_type
        allowed_types = ["channel", "advertisement", "movie", "series", "episode", "ott", "music", "unknown"]
        if content_type not in allowed_types:
            if content_type == "song":
                content_type = "music"
            else:
                content_type = "unknown"

        # Adaptive threshold based on available modalities
        match_threshold = 0.60 if (audio_missing or vis_score >= 0.60) else 0.75
        
        # Lower threshold for continuous progression to improve stabilization
        if best_match.get("is_continuous"):
            match_threshold = 0.50

        if confidence < match_threshold:
            return RecognitionResult(
                content_name="Unknown Content",
                content_type="unknown",
                confidence=confidence,
                breakdown=MatchBreakdown(
                    visual_score=vis_score,
                    audio_score=aud_score,
                    ocr_score=0.0,
                    logo_score=0.0
                ),
                matched_channel=None,
                playback_position=None
            )

        # Check for shared intro/outro across different episodes of the same series
        if content_type == "series" and content.series_name:
            matching_episodes = set()
            for m in matches:
                if m["score"] >= 0.55 and m["content"].content_type == "series" and m["content"].series_name == content.series_name:
                    if m["content"].episode_number is not None:
                        matching_episodes.add(m["content"].episode_number)
            if len(matching_episodes) >= 3:
                return RecognitionResult(
                    content_id=f"series-{content.series_name.lower().replace(' ', '_')}-generic",
                    content_name=f"{content.series_name} (Intro)",
                    content_type="series",
                    confidence=confidence,
                    breakdown=MatchBreakdown(
                        visual_score=vis_score,
                        audio_score=aud_score,
                        ocr_score=0.0,
                        logo_score=0.0
                    ),
                    matched_channel=None,
                    playback_position=None,
                    platform=content.platform_id,
                    series=content.series_name,
                    season=None,
                    episode=None
                )

        return RecognitionResult(
            content_id=content.content_id,
            content_name=content.title,
            content_type=content_type,
            confidence=confidence,
            breakdown=MatchBreakdown(
                visual_score=vis_score,
                audio_score=aud_score,
                ocr_score=0.0,
                logo_score=0.0
            ),
            matched_channel=None,
            playback_position=playback_pos,
            platform=content.platform_id,
            series=content.series_name,
            season=content.season_number,
            episode=content.episode_number
        )

    # No winner found
    return RecognitionResult(
        content_name="Unknown Content",
        content_type="unknown",
        confidence=0.0,
        breakdown=MatchBreakdown(visual_score=0.0, audio_score=0.0, ocr_score=0.0, logo_score=0.0),
        matched_channel=None,
        playback_position=None
    )
