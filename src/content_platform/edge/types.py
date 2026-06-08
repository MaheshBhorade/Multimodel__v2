from dataclasses import dataclass

from content_platform.shared.models import FingerprintPayload


@dataclass
class EdgeCapture:
    payload: FingerprintPayload
    snapshot_bytes: bytes | None = None
    snapshot_filename: str | None = None
