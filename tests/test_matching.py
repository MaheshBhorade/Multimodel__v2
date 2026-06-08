import pytest
from sqlalchemy.orm import Session
from content_platform.server.matching import audio_similarity, cosine_like_similarity, ocr_similarity, match_content
from content_platform.server.models import ContentLibrary
from content_platform.shared.models import FingerprintPayload
from datetime import datetime


def test_visual_similarity_prefers_close_vectors() -> None:
    # Testing that float precision rounding works and returns exactly 1.0
    # Use 3-dimensional vectors so they remain multi-dimensional after excluding the DC component
    assert cosine_like_similarity([0.5, 0.9, 0.1], [0.5, 0.9, 0.1]) == 1.0
    assert cosine_like_similarity([0.5, 0.9, 0.1], [0.5, 0.1, 0.9]) < 0.5


def test_audio_similarity_exact_match_scores_highest() -> None:
    # Legacy string format matching
    assert audio_similarity("dilwale-main-track", "dilwale-main-track") == 1.0
    assert audio_similarity("sony-max-theme", "sony-radio-theme") == 0.35

    # Float list matching
    v1 = [1.0, 2.0, 3.0]
    v2 = [1.0, 2.0, 3.0]
    assert audio_similarity(v1, v2) == 1.0
    
    # Cosine difference
    assert audio_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0


def test_ocr_similarity_detects_keyword_overlap() -> None:
    score = ocr_similarity("dilwale shah rukh", "dilwale shah rukh kajol")
    assert score > 0.5


class MockDB:
    def __init__(self, items):
        self.items = items

    def scalars(self, query):
        class MockQuery:
            def __init__(self, items):
                self.items = items
            def all(self):
                return self.items
        return MockQuery(self.items)


def test_match_content_segment_voting():
    # Setup some mock library items
    import json
    
    # 256-dim visual and 13-dim audio
    vis1 = [0.0, 1.0] + [0.0] * 254
    vis2 = [0.0, 0.0, 1.0] + [0.0] * 253
    
    aud = [1.0] + [0.0] * 129
    
    # bangles items
    bangles_items = [
        ContentLibrary(
            id=i,
            external_content_id="bangles-id",
            title="Bangles",
            category="music",
            segment_offset=i * 10,
            visual_fp=json.dumps(vis1),
            audio_fp=json.dumps(aud)
        )
        for i in range(7)
    ]
    
    # shararat items
    shararat_items = [
        ContentLibrary(
            id=10 + i,
            external_content_id="shararat-id",
            title="Shararat",
            category="music",
            segment_offset=i * 10,
            visual_fp=json.dumps(vis2),
            audio_fp=json.dumps(aud)
        )
        for i in range(3)
    ]
    
    db = MockDB(bangles_items + shararat_items)
    
    # Payload matches vis1 perfectly
    payload = FingerprintPayload(
        device_id="PI001",
        timestamp=datetime.utcnow(),
        visual_fps=[vis1] * 10,
        audio_fp=aud
    )
    
    # match_content should return Bangles as winner because it gets 7 votes vs 3 votes
    result = match_content(db, payload)
    
    assert result.content_name == "Bangles"
    assert result.content_type == "music"
    assert result.confidence >= 0.75
