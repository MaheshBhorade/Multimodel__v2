import pytest
from sqlalchemy.orm import Session
from content_platform.server.matching import audio_similarity, cosine_like_similarity, ocr_similarity, match_content
from content_platform.server.models import Content, ContentSegment
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
    def __init__(self, segments, contents):
        self.segments = segments
        self.contents = contents

    def query(self, model_class):
        class MockQuery:
            def __init__(self, parent, model_class):
                self.parent = parent
                self.model_class = model_class
            def filter(self, *args, **kwargs):
                return self
            def join(self, *args, **kwargs):
                return self
            def order_by(self, *args, **kwargs):
                return self
            def first(self):
                return None
            def all(self):
                if self.model_class.__name__ == "ContentSegment":
                    return self.parent.segments
                if self.model_class.__name__ == "Content":
                    return self.parent.contents
                return []
        return MockQuery(self, model_class)

    def scalars(self, query):
        class MockQuery:
            def __init__(self, parent, query):
                self.parent = parent
                self.query = query
            def all(self):
                return self.parent.contents
            def first(self):
                try:
                    params = self.query.compile().params
                    for val in params.values():
                        for c in self.parent.contents:
                            if c.content_id == val:
                                return c
                except Exception:
                    pass
                q_str = str(self.query)
                for c in self.parent.contents:
                    if c.content_id in q_str:
                        return c
                return self.parent.contents[0] if self.parent.contents else None
        return MockQuery(self, query)


def test_match_content_segment_voting():
    # Setup some mock library items
    import json
    
    # 256-dim visual and 13-dim audio
    vis1 = [0.0, 1.0] + [0.0] * 254
    vis2 = [0.0, 0.0, 1.0] + [0.0] * 253
    
    aud = [1.0] + [0.0] * 129
    
    bangles_content = Content(
        content_id="bangles-id",
        title="Bangles",
        content_type="music"
    )
    shararat_content = Content(
        content_id="shararat-id",
        title="Shararat",
        content_type="music"
    )

    # bangles items
    bangles_items = [
        ContentSegment(
            segment_id=i,
            content_id="bangles-id",
            segment_index=i,
            segment_offset=i * 10,
            visual_fp=json.dumps(vis1),
            audio_fp=json.dumps(aud),
            content=bangles_content
        )
        for i in range(7)
    ]
    
    # shararat items
    shararat_items = [
        ContentSegment(
            segment_id=10 + i,
            content_id="shararat-id",
            segment_index=i,
            segment_offset=i * 10,
            visual_fp=json.dumps(vis2),
            audio_fp=json.dumps(aud),
            content=shararat_content
        )
        for i in range(3)
    ]
    
    db = MockDB(bangles_items + shararat_items, [bangles_content, shararat_content])
    
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


def test_match_content_detects_intro() -> None:
    import json
    
    # 256-dim visual and 13-dim audio
    vis1 = [0.0, 1.0] + [0.0] * 254
    aud = [1.0] + [0.0] * 129
    
    goyamart_ep1 = Content(
        content_id="series-goyamart-s01-e01",
        title="Goyamart S01E01",
        content_type="series",
        series_name="Goyamart",
        season_number=1,
        episode_number=1,
        platform_id="youtube"
    )
    goyamart_ep2 = Content(
        content_id="series-goyamart-s01-e02",
        title="Goyamart S01E02",
        content_type="series",
        series_name="Goyamart",
        season_number=1,
        episode_number=2,
        platform_id="youtube"
    )
    goyamart_ep3 = Content(
        content_id="series-goyamart-s01-e03",
        title="Goyamart S01E03",
        content_type="series",
        series_name="Goyamart",
        season_number=1,
        episode_number=3,
        platform_id="youtube"
    )

    # Ingest same segment for all three episodes (represents the shared intro)
    ep1_seg = ContentSegment(
        segment_id=1,
        content_id="series-goyamart-s01-e01",
        segment_index=0,
        segment_offset=0,
        visual_fp=json.dumps(vis1),
        audio_fp=json.dumps(aud),
        content=goyamart_ep1
    )
    ep2_seg = ContentSegment(
        segment_id=2,
        content_id="series-goyamart-s01-e02",
        segment_index=0,
        segment_offset=0,
        visual_fp=json.dumps(vis1),
        audio_fp=json.dumps(aud),
        content=goyamart_ep2
    )
    ep3_seg = ContentSegment(
        segment_id=3,
        content_id="series-goyamart-s01-e03",
        segment_index=0,
        segment_offset=0,
        visual_fp=json.dumps(vis1),
        audio_fp=json.dumps(aud),
        content=goyamart_ep3
    )
    
    db = MockDB([ep1_seg, ep2_seg, ep3_seg], [goyamart_ep1, goyamart_ep2, goyamart_ep3])
    
    # Payload matches the shared intro perfectly
    payload = FingerprintPayload(
        device_id="PI001",
        timestamp=datetime.utcnow(),
        visual_fps=[vis1] * 10,
        audio_fp=aud
    )
    
    result = match_content(db, payload)
    
    # It should identify it as the series intro
    assert result.content_name == "Goyamart (Intro)"
    assert result.content_type == "series"
    assert result.content_id == "series-goyamart-generic"
    assert result.episode is None
    assert result.season is None
