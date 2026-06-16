from datetime import UTC, datetime
import json
from fastapi.testclient import TestClient
from content_platform.server.main import app


def test_list_devices() -> None:
    with TestClient(app) as client:
        # Fetch devices
        response = client.get("/api/v1/devices")
        assert response.status_code == 200
        devices = response.json()
        assert isinstance(devices, list)
        
        # Verify schema elements
        for device in devices:
            assert "device_id" in device
            assert "status" in device
            assert "capture_count" in device
            assert "last_active" in device


def test_list_captures() -> None:
    with TestClient(app) as client:
        # Get all captures
        response = client.get("/api/v1/captures")
        assert response.status_code == 200
        body = response.json()
        assert "total" in body
        assert "items" in body
        assert isinstance(body["items"], list)

        # Ingest an unknown capture first
        resp = client.post(
            "/api/v1/captures",
            json={
                "device_id": "PI001",
                "timestamp": datetime.now(UTC).isoformat(),
                "visual_fps": [[0.01] * 960] * 10,
                "audio_fp": [0.05] * 130,
                "snapshot_url": "http://localhost/snapshot.jpg",
            },
        )
        assert resp.status_code == 200

        # Get only unknown captures
        response_unknown = client.get("/api/v1/captures?only_unknown=true")
        assert response_unknown.status_code == 200
        body_unk = response_unknown.json()
        assert len(body_unk["items"]) > 0
        for item in body_unk["items"]:
            assert item["result"]["content_type"] == "unknown"


def test_library_crud_endpoints() -> None:
    with TestClient(app) as client:
        # Create an item in the reference content library
        new_item = {
            "title": "Test Channel",
            "category": "channel",
            "channel_name": "Test Network",
            "visual_fp": [0.1] * 960,
            "audio_fp": [0.2] * 130,
            "logo_fp": [0.5] * 128,
            "ocr_keywords": "test channel live broadcast"
        }
        
        create_resp = client.post("/api/v1/library", json=new_item)
        assert create_resp.status_code == 200
        created = create_resp.json()
        assert created["title"] == "Test Channel"
        assert "id" in created
        assert "external_content_id" in created
        
        item_id = created["id"]
        
        # Fetch library and verify the new item is there
        get_resp = client.get("/api/v1/library")
        assert get_resp.status_code == 200
        library = get_resp.json()
        assert any(item["id"] == item_id for item in library)
        
        # Delete the library item
        del_resp = client.delete(f"/api/v1/library/{item_id}")
        assert del_resp.status_code == 200
        assert del_resp.json() == {"status": "deleted"}
        
        # Verify it's deleted
        get_resp_after = client.get("/api/v1/library")
        library_after = get_resp_after.json()
        assert not any(item["id"] == item_id for item in library_after)


def test_resolve_capture() -> None:
    with TestClient(app) as client:
        # 1. Ingest an unknown capture so we have a capture ID to resolve
        ingest_resp = client.post(
            "/api/v1/captures",
            json={
                "device_id": "TEST_PI_001",
                "timestamp": datetime.now(UTC).isoformat(),
                "visual_fps": [[0.01] * 960] * 10,
                "audio_fp": [0.05] * 130,
                "snapshot_url": "http://localhost/snapshots/test.jpg"
            }
        )
        assert ingest_resp.status_code == 200
        body = ingest_resp.json()
        capture_id = body["capture_id"]
        assert body["result"]["content_name"] == "Matching in progress..."
        
        # 2. Resolve this unknown capture
        resolve_payload = {
            "title": "Newly Registered Show",
            "category": "series",
            "channel_name": "Test Series Hub"
        }
        resolve_resp = client.post(
            f"/api/v1/captures/{capture_id}/resolve",
            json=resolve_payload
        )
        assert resolve_resp.status_code == 200
        assert resolve_resp.json()["status"] == "resolved"
        
        # 3. Verify that the capture record is now updated to the resolved value
        caps_resp = client.get("/api/v1/captures")
        caps = caps_resp.json()["items"]
        resolved_capture = next(c for c in caps if c["id"] == capture_id)
        
        assert resolved_capture["result"]["content_name"] == "Newly Registered Show"
        assert resolved_capture["result"]["content_type"] == "series"
        assert resolved_capture["result"]["confidence"] == 1.0
        
        # 4. Clean up: find the newly added item in content library and delete it
        lib_resp = client.get("/api/v1/library")
        lib_items = lib_resp.json()
        added_lib_item = next(item for item in lib_items if item["title"] == "Newly Registered Show")
        client.delete(f"/api/v1/library/{added_lib_item['id']}")


def test_analytics_endpoints() -> None:
    with TestClient(app) as client:
        # Overview
        resp = client.get("/api/v1/analytics/overview")
        assert resp.status_code == 200
        overview = resp.json()
        assert "total_captures" in overview
        assert "active_devices" in overview
        assert "pending_reviews" in overview

        # Share
        resp = client.get("/api/v1/analytics/share")
        assert resp.status_code == 200
        share = resp.json()
        assert isinstance(share, dict)

        # Ad Frequency
        resp = client.get("/api/v1/analytics/ad-frequency")
        assert resp.status_code == 200
        ads = resp.json()
        assert isinstance(ads, list)

        # Timeline
        resp = client.get("/api/v1/analytics/timeline")
        assert resp.status_code == 200
        timeline = resp.json()
        assert isinstance(timeline, list)


def test_vector_store_fallback() -> None:
    from content_platform.server.vector_store import QdrantAdapter
    adapter = QdrantAdapter()
    
    adapter.upsert_reference("test-fallback-id", [1.0] * 64, [1.0] * 16)
    
    res = adapter.search_visual([1.0] * 64, limit=1)
    assert len(res) == 1
    assert res[0]["content_id"] == "test-fallback-id"
    assert res[0]["score"] == 1.0

    adapter.delete_reference("test-fallback-id")
    res_after = adapter.search_visual([1.0] * 64, limit=1)
    assert len(res_after) == 0
