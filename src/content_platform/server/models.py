from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from content_platform.server.db import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active")

    captures: Mapped[list["Capture"]] = relationship(back_populates="device")


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
        uselist=False
    )


class ContentLibrary(Base):
    __tablename__ = "content_library"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Multiple segments can belong to same content
    external_content_id: Mapped[str] = mapped_column(
        String(128),
        index=True
    )

    title: Mapped[str] = mapped_column(
        String(255),
        index=True
    )

    category: Mapped[str] = mapped_column(
        String(64),
        index=True
    )

    channel_name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True
    )

    segment_offset: Mapped[int] = mapped_column(
        Integer,
        default=0,
        index=True
    )

    visual_fp: Mapped[str] = mapped_column(Text)

    audio_fp: Mapped[str] = mapped_column(Text)

    logo_fp: Mapped[str] = mapped_column(
        Text,
        default="[]"
    )

    ocr_keywords: Mapped[str] = mapped_column(
        Text,
        default=""
    )


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

    capture: Mapped["Capture"] = relationship(
        back_populates="result"
    )