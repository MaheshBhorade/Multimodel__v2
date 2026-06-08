from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class FingerprintPayload(BaseModel):
    device_id: str = Field(min_length=2)
    timestamp: datetime
    
    # Array of 10 visual fingerprints (1 per frame)
    visual_fps: list[list[float]] = Field(default_factory=list)  # 10 x 256 dims
    
    # Single audio fingerprint for 10-sec segment
    audio_fp: list[float] = Field(default_factory=list)  # 13 MFCC coefficients
    
    # Best-quality frame image
    snapshot_url: str | None = None
    
    # Batch metadata
    batch_count: int = Field(default=10, ge=1)
    batch_duration_sec: int = Field(default=10, ge=1)
    best_frame_index: int = Field(default=0, ge=0, le=9)
    confidence_visual: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence_audio: float = Field(default=0.5, ge=0.0, le=1.0)


class MatchBreakdown(BaseModel):
    visual_score: float = Field(ge=0.0, le=1.0)
    audio_score: float = Field(ge=0.0, le=1.0)
    ocr_score: float = Field(ge=0.0, le=1.0)
    logo_score: float = Field(ge=0.0, le=1.0)


class RecognitionResult(BaseModel):
    content_id: str | None = None
    content_name: str
    content_type: Literal["channel", "advertisement", "movie", "series", "episode", "ott", "music", "unknown"]
    confidence: float = Field(ge=0.0, le=1.0)
    breakdown: MatchBreakdown
    matched_channel: str | None = None


class CaptureResponse(BaseModel):
    capture_id: int
    result: RecognitionResult


class SnapshotUploadResponse(BaseModel):
    snapshot_url: str
