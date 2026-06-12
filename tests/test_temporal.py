from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from content_platform.server.db import Base
from content_platform.server.models import Device, Capture, RecognitionResultRecord, PlaybackSession
from content_platform.server.temporal import rebuild_playback_sessions_for_device

# Use an in-memory SQLite database to avoid locking conflicts with the active server
test_engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

# Ensure all database tables exist before running tests
Base.metadata.create_all(bind=test_engine)


def test_temporal_state_machine_sessions() -> None:
    with TestSessionLocal() as db:
        # Create a unique test device
        device_id = "TEST_DEVICE_TEMPORAL_99"
        
        # Clean up any residual data first
        db.query(PlaybackSession).filter(PlaybackSession.device_id == device_id).delete()
        old_device = db.query(Device).filter(Device.device_id == device_id).first()
        if old_device:
            db.query(Capture).filter(Capture.device_id == old_device.id).delete()
            db.delete(old_device)
        db.commit()

        device = Device(device_id=device_id, status="active")
        db.add(device)
        db.flush()

        base_time = datetime.now(UTC)

        # Helper to add a capture with match
        def add_capture(offset_seconds: int, content_name: str, content_type: str) -> None:
            cap = Capture(
                device_id=device.id,
                captured_at=base_time + timedelta(seconds=offset_seconds),
                visual_fp="[]",
                audio_fp="[]",
                status="matched"
            )
            db.add(cap)
            db.flush()

            result = RecognitionResultRecord(
                capture_id=cap.id,
                content_name=content_name,
                content_type=content_type,
                confidence=0.9,
                visual_score=0.9,
                audio_score=0.9,
                ocr_score=0.0,
                logo_score=0.0
            )
            db.add(result)
            db.flush()

        # ----------------------------------------------------
        # Scenario 1: Play content X for 11 times.
        # Should NOT trigger a playback session (threshold is 12).
        # ----------------------------------------------------
        for i in range(11):
            add_capture(i * 10, "goyamart", "series")
        db.commit()

        rebuild_playback_sessions_for_device(db, device_id)
        sessions = db.query(PlaybackSession).filter(PlaybackSession.device_id == device_id).all()
        assert len(sessions) == 0, "Should not start session with only 11 consecutive matches"

        # ----------------------------------------------------
        # Scenario 2: Play content X for the 12th time.
        # Should now start a session for X.
        # ----------------------------------------------------
        add_capture(11 * 10, "goyamart", "series")
        db.commit()

        rebuild_playback_sessions_for_device(db, device_id)
        sessions = db.query(PlaybackSession).filter(PlaybackSession.device_id == device_id).all()
        assert len(sessions) == 1
        assert sessions[0].content_name == "goyamart"
        assert sessions[0].entry_count == 12
        assert sessions[0].duration_seconds == 110.0  # 11 intervals of 10s

        # ----------------------------------------------------
        # Scenario 3: Play content X for 5 more times, then a brief noise/different content Y for 3 times.
        # The session for X should continue playing since Y didn't reach threshold 10.
        # ----------------------------------------------------
        for i in range(12, 17):
            add_capture(i * 10, "goyamart", "series")
        for i in range(17, 20):
            add_capture(i * 10, "shararat", "song")
        db.commit()

        rebuild_playback_sessions_for_device(db, device_id)
        sessions = db.query(PlaybackSession).filter(PlaybackSession.device_id == device_id).all()
        assert len(sessions) == 1
        assert sessions[0].content_name == "goyamart"
        # Since active session remained goyamart, and ending was not triggered by candidate, 
        # it includes the goyamart entries. Entry count is 17 (12 + 5).
        assert sessions[0].entry_count == 17

        # ----------------------------------------------------
        # Scenario 4: Now play content Y for 7 more times (making it 10 consecutive entries of Y).
        # This should end session for X and start a session for Y.
        # ----------------------------------------------------
        for i in range(20, 27):
            add_capture(i * 10, "shararat", "song")
        db.commit()

        rebuild_playback_sessions_for_device(db, device_id)
        sessions = db.query(PlaybackSession).filter(PlaybackSession.device_id == device_id).order_by(PlaybackSession.start_time.asc()).all()
        assert len(sessions) == 2
        assert sessions[0].content_name == "goyamart"
        assert sessions[1].content_name == "shararat"
        assert sessions[1].entry_count == 10

        # ----------------------------------------------------
        # Scenario 5: Now play "unknown" for 10 times consecutively.
        # This should end the session for Y and return to IDLE.
        # ----------------------------------------------------
        for i in range(27, 37):
            add_capture(i * 10, "unknown", "unknown")
        db.commit()

        rebuild_playback_sessions_for_device(db, device_id)
        sessions = db.query(PlaybackSession).filter(PlaybackSession.device_id == device_id).order_by(PlaybackSession.start_time.asc()).all()
        # Still 2 sessions, since unknown doesn't start a new session, it just ends Y.
        assert len(sessions) == 2
        assert sessions[0].content_name == "goyamart"
        assert sessions[1].content_name == "shararat"

        # Cleanup
        db.query(PlaybackSession).filter(PlaybackSession.device_id == device_id).delete()
        db.query(Capture).filter(Capture.device_id == device.id).delete()
        db.delete(device)
        db.commit()
