from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from content_platform.server.db import Base, PlatformBase


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")

    captures: Mapped[list["Capture"]] = relationship(back_populates="device", cascade="all, delete-orphan")


class Capture(Base):
    __tablename__ = "captures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id"),
        index=True
    )

    captured_at: Mapped[datetime] = mapped_column(
        DateTime,
        index=True
    )

    visual_fp: Mapped[str] = mapped_column(Text)
    audio_fp: Mapped[str] = mapped_column(Text)

    logo_fp: Mapped[str] = mapped_column(
        Text,
        default="[]"
    )

    ocr_text: Mapped[str] = mapped_column(
        Text,
        default=""
    )

    snapshot_url: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(32),
        default="pending"
    )

    device: Mapped["Device"] = relationship(
        back_populates="captures"
    )

    result: Mapped["RecognitionResultRecord"] = relationship(
        back_populates="capture",
        uselist=False,
        cascade="all, delete-orphan"
    )


class Content(Base):
    __tablename__ = "content"

    content_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    platform_id: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    content_type: Mapped[str] = mapped_column(String(64), index=True) # movie or series
    series_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    season_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    release_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    language: Mapped[str | None] = mapped_column(String(64), nullable=True)
    genre: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    segments: Mapped[list["ContentSegment"]] = relationship(back_populates="content", cascade="all, delete-orphan")


class ContentSegment(Base):
    __tablename__ = "content_segments"

    segment_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content_id: Mapped[str] = mapped_column(String(128), ForeignKey("content.content_id"), index=True)
    segment_index: Mapped[int] = mapped_column(Integer, index=True)
    segment_offset: Mapped[int] = mapped_column(Integer, index=True)
    visual_fp: Mapped[str] = mapped_column(Text)
    audio_fp: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    content: Mapped["Content"] = relationship(back_populates="segments")


class RecognitionResultRecord(Base):
    __tablename__ = "recognition_results"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True
    )

    capture_id: Mapped[int] = mapped_column(
        ForeignKey("captures.id"),
        unique=True
    )

    content_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    content_name: Mapped[str] = mapped_column(
        String(255)
    )

    content_type: Mapped[str] = mapped_column(
        String(64)
    )

    confidence: Mapped[float] = mapped_column(Float)

    visual_score: Mapped[float] = mapped_column(Float)
    audio_score: Mapped[float] = mapped_column(Float)
    ocr_score: Mapped[float] = mapped_column(Float)
    logo_score: Mapped[float] = mapped_column(Float)
    matched_platform: Mapped[str] = mapped_column(String(64), default="unknown", server_default="unknown")
    matched_channel: Mapped[str | None] = mapped_column(String(255), nullable=True)
    series: Mapped[str | None] = mapped_column(String(255), nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    playback_position: Mapped[float | None] = mapped_column(Float, nullable=True)

    capture: Mapped["Capture"] = relationship(
        back_populates="result"
    )


class PlaybackSession(Base):
    __tablename__ = "playback_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), index=True)
    content_name: Mapped[str] = mapped_column(String(255), index=True)
    content_type: Mapped[str] = mapped_column(String(64), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    end_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_seconds: Mapped[float] = mapped_column(Float)
    entry_count: Mapped[int] = mapped_column(Integer)


class PlatformReference(PlatformBase):
    __tablename__ = "platform_reference"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    platform_id: Mapped[str] = mapped_column(String(128), index=True)
    platform_name: Mapped[str] = mapped_column(String(255), index=True)
    platform_type: Mapped[str] = mapped_column(String(64), index=True) # OTT or TV
    logo_fp: Mapped[str] = mapped_column(Text, default="[]")
    visual_fp: Mapped[str] = mapped_column(Text)
    ocr_keywords: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)