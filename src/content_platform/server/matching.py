import json
from dataclasses import dataclass
import numpy as np
from pathlib import Path
from content_platform.server.faiss_index import FAISSIndex

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

# FAISS index configuration
FAISS_VISUAL_DIM = 960
FAISS_AUDIO_DIM = 13
FAISS_INDEX_DIR = Path("runtime/faiss")
FAISS_INDEX_DIR.mkdir(parents=True, exist_ok=True)
VISUAL_INDEX_PATH = FAISS_INDEX_DIR / "visual.index"
VISUAL_META_PATH = FAISS_INDEX_DIR / "visual_meta.json"
AUDIO_INDEX_PATH = FAISS_INDEX_DIR / "audio.index"
AUDIO_META_PATH = FAISS_INDEX_DIR / "audio_meta.json"

if VISUAL_INDEX_PATH.exists() and VISUAL_META_PATH.exists():
    visual_index = FAISSIndex.load(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH), dim=FAISS_VISUAL_DIM, gpu=True)
else:
    visual_index = None

if AUDIO_INDEX_PATH.exists() and AUDIO_META_PATH.exists():
    audio_index = FAISSIndex.load(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH), dim=FAISS_AUDIO_DIM, gpu=True)
else:
    audio_index = None

from content_platform.server.models import ContentLibrary
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


def match_content(db: Session, payload: FingerprintPayload) -> RecognitionResult:
    # 1. Fetch all segments from DB
    contents = db.scalars(select(ContentLibrary)).all()
    if not contents:
        return RecognitionResult(
            content_name="Unknown Content",
            content_type="unknown",
            confidence=0.0,
            breakdown=MatchBreakdown(visual_score=0.0, audio_score=0.0, ocr_score=0.0, logo_score=0.0),
            matched_channel=None,
        )

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

    # Use FAISS indexes when available; otherwise fallback to brute‑force.
    matches = []
    if visual_index is not None and audio_index is not None and not is_payload_blank and not audio_missing:
        # Prepare query vectors
        if isinstance(payload.visual_fps[0], list):
            avg_visual = np.mean(np.array(payload.visual_fps, dtype=np.float32), axis=0)
        else:
            avg_visual = np.array(payload.visual_fps, dtype=np.float32)
        audio_vec = np.array(payload.audio_fp, dtype=np.float32)
        # Search indexes (k=10)
        visual_results = visual_index.search(avg_visual, k=10)
        audio_results = audio_index.search(audio_vec, k=10)
        visual_scores = {cid: 1/(1+dist) for cid, dist in visual_results}
        audio_scores = {cid: 1/(1+dist) for cid, dist in audio_results}
        # Combine scores per content
        all_cids = set(visual_scores.keys()) | set(audio_scores.keys())
        for cid in all_cids:
            vis_sim = visual_scores.get(cid, 0.0)
            aud_sim = audio_scores.get(cid, 0.0)
            # Adaptive scoring same as original logic
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
            # Retrieve content object from DB
            content_obj = db.execute(select(ContentLibrary).where(ContentLibrary.external_content_id == cid)).scalar_one_or_none()
            if content_obj:
                matches.append({
                    "content": content_obj,
                    "score": combined_score,
                    "visual_score": vis_sim,
                    "audio_score": aud_sim,
                })
    else:
        # Fallback to original brute‑force method
        for content in contents:
            # Load library vectors
            try:
                lib_visual = json.loads(content.visual_fp)
            except Exception:
                lib_visual = []
            
            try:
                lib_audio = json.loads(content.audio_fp)
            except Exception:
                lib_audio = content.audio_fp  # String fallback

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

            matches.append({
                "content": content,
                "score": combined_score,
                "visual_score": vis_sim,
                "audio_score": aud_sim,
            })

    # Sort matches by score descending
    matches.sort(key=lambda x: x["score"], reverse=True)

    # Take top 10 matches
    top_10 = matches[:10]

    # Group by external_content_id
    votes = {}
    for m in top_10:
        cid = m["content"].external_content_id
        if cid not in votes:
            votes[cid] = []
        votes[cid].append(m)

    # Segment Voting: choose winner with highest vote count, tie-break by max individual score
    winner_cid = None
    max_votes = -1
    best_winner_score = -1

    for cid, group_matches in votes.items():
        vote_count = len(group_matches)
        group_max_score = max(m["score"] for m in group_matches)
        if vote_count > max_votes or (vote_count == max_votes and group_max_score > best_winner_score):
            max_votes = vote_count
            winner_cid = cid
            best_winner_score = group_max_score

    # Construct final result
    if winner_cid is not None:
        winning_group = votes[winner_cid]
        best_match = max(winning_group, key=lambda x: x["score"])
        content = best_match["content"]
        confidence = best_match["score"]
        vis_score = best_match["visual_score"]
        aud_score = best_match["audio_score"]

        # Map "song" or other custom categories to valid Pydantic type Literal
        content_type = content.category
        allowed_types = ["channel", "advertisement", "movie", "series", "episode", "ott", "music", "unknown"]
        if content_type not in allowed_types:
            if content_type == "song":
                content_type = "music"
            else:
                content_type = "unknown"

        # Adaptive threshold based on available modalities:
        #   - Visual-only (audio broken):  0.60  (cross-song max is 0.42, safe margin)
        #   - Full multimodal:             0.75  (standard threshold)
        match_threshold = 0.60 if audio_missing else 0.75

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
            )

        return RecognitionResult(
            content_id=content.external_content_id,
            content_name=content.title,
            content_type=content_type,
            confidence=confidence,
            breakdown=MatchBreakdown(
                visual_score=vis_score,
                audio_score=aud_score,
                ocr_score=0.0,
                logo_score=0.0
            ),
            matched_channel=content.channel_name,
        )

    # No winner found
    return RecognitionResult(
        content_name="Unknown Content",
        content_type="unknown",
        confidence=0.0,
        breakdown=MatchBreakdown(visual_score=0.0, audio_score=0.0, ocr_score=0.0, logo_score=0.0),
        matched_channel=None,
    )
