from datetime import UTC, datetime
import json
from pathlib import Path
from fastapi.testclient import TestClient

from content_platform.server.main import app
from content_platform.server.db import SessionLocal
from content_platform.server.models import ContentLibrary


def test_ingest_capture_returns_recognition_result() -> None:
    # Get a real segment from the database to ensure we get a match
    with SessionLocal() as db:
        segment = next(item for item in db.query(ContentLibrary) if any(x != 0.0 for x in json.loads(item.visual_fp)))
        assert segment is not None, "ContentLibrary should have seeded segments"
        
        ref_title = segment.title
        ref_visual = json.loads(segment.visual_fp)
        ref_audio = json.loads(segment.audio_fp)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/captures",
            json={
                "device_id": "PI001",
                "timestamp": datetime.now(UTC).isoformat(),
                "visual_fps": [ref_visual] * 10,
                "audio_fp": ref_audio,
                "snapshot_url": "http://localhost/snapshot.jpg",
            },
        )

        assert response.status_code == 200
        body = response.json()
        assert body["result"]["content_name"] == "Matching in progress..."
        
        # Verify background task has processed the match in DB
        caps = client.get("/api/v1/captures").json()["items"]
        latest = next(c for c in caps if c["id"] == body["capture_id"])
        
        # Should match the reference song name
        assert latest["result"]["content_name"] == ref_title
        assert latest["result"]["confidence"] >= 0.8


def test_snapshot_upload_persists_file() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/snapshots",
            data={"device_id": "PI001", "timestamp": "2026-06-05T10-10-10Z"},
            files={"file": ("frame.jpg", b"test-image", "image/jpeg")},
        )

        assert response.status_code == 200
        body = response.json()
        assert "/snapshots/PI001/" in body["snapshot_url"]

        relative = body["snapshot_url"].split("/snapshots/", 1)[1]
        stored = Path("runtime/snapshots") / relative
        assert stored.exists()
