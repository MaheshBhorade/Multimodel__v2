import httpx

from content_platform.shared.models import CaptureResponse, FingerprintPayload, SnapshotUploadResponse


class ServerClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def submit_capture(self, payload: FingerprintPayload) -> CaptureResponse:
        with httpx.Client(timeout=10.0) as client:
            response = client.post(f"{self.base_url}/api/v1/captures", json=payload.model_dump(mode="json"))
            response.raise_for_status()
            return CaptureResponse.model_validate(response.json())

    def upload_snapshot(self, device_id: str, timestamp: str, filename: str, content: bytes) -> SnapshotUploadResponse:
        with httpx.Client(timeout=20.0) as client:
            response = client.post(
                f"{self.base_url}/api/v1/snapshots",
                data={"device_id": device_id, "timestamp": timestamp},
                files={"file": (filename, content, "image/jpeg")},
            )
            response.raise_for_status()
            return SnapshotUploadResponse.model_validate(response.json())
