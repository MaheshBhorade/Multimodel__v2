import logging
import time

from content_platform.edge.extractors import FingerprintExtractor
from content_platform.edge.transport import ServerClient
from content_platform.shared.config import get_settings

logger = logging.getLogger(__name__)


class EdgeAgent:
    def __init__(self) -> None:
        settings = get_settings()
        self.device_id = settings.device_id
        self.interval = settings.capture_interval_seconds
        self.extractor = FingerprintExtractor()
        self.client = ServerClient(settings.edge_server_url)

    def run_forever(self) -> None:
        while True:
            capture = self.extractor.capture(self.device_id)
            if capture is None:
                time.sleep(1)
                continue

            if capture.snapshot_bytes and capture.snapshot_filename:
                snapshot = self.client.upload_snapshot(
                    self.device_id,
                    capture.payload.timestamp.isoformat(),
                    capture.snapshot_filename,
                    capture.snapshot_bytes,
                )
                capture.payload.snapshot_url = snapshot.snapshot_url

            result = self.client.submit_capture(capture.payload)
            logger.info(
                "capture_id=%s content=%s type=%s confidence=%.2f",
                result.capture_id,
                result.result.content_name,
                result.result.content_type,
                result.result.confidence,
            )
            time.sleep(1)
