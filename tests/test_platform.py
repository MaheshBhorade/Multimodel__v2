import json
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from content_platform.server.db import PlatformBase
from content_platform.server.models import PlatformLibrary
from content_platform.server.matching import match_platform
from content_platform.shared.models import FingerprintPayload
from datetime import datetime


def test_platform_matching():
    # 1. Setup in-memory platform database
    engine = create_engine("sqlite:///:memory:")
    PlatformBase.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    session = TestingSessionLocal()
    
    import content_platform.server.matching as matching
    from pathlib import Path
    matching.platform_visual_index = None
    matching.PLATFORM_VISUAL_INDEX_PATH = Path("nonexistent_test.index")
    matching.PLATFORM_VISUAL_META_PATH = Path("nonexistent_test_meta.json")
    
    # 2. Seed mock platforms (960-dim vectors)
    # YouTube visual fingerprint pattern: mostly 1.0 at index 5
    youtube_vec = [0.0] * 960
    youtube_vec[5] = 1.0
    
    # Netflix visual fingerprint pattern: mostly 1.0 at index 20
    netflix_vec = [0.0] * 960
    netflix_vec[20] = 1.0
    
    session.add(PlatformLibrary(
        platform_name="YouTube",
        segment_offset=0,
        visual_fp=json.dumps(youtube_vec)
    ))
    session.add(PlatformLibrary(
        platform_name="Netflix",
        segment_offset=0,
        visual_fp=json.dumps(netflix_vec)
    ))
    session.commit()
    
    # 3. Test matching YouTube layout
    payload_youtube = FingerprintPayload(
        device_id="TEST01",
        timestamp=datetime.utcnow(),
        visual_fps=[youtube_vec] * 10,
        audio_fp=[0.0] * 130
    )
    
    result_youtube = match_platform(session, payload_youtube)
    assert result_youtube == "YouTube"
    
    # 4. Test matching Netflix layout
    payload_netflix = FingerprintPayload(
        device_id="TEST01",
        timestamp=datetime.utcnow(),
        visual_fps=[netflix_vec] * 10,
        audio_fp=[0.0] * 130
    )
    
    result_netflix = match_platform(session, payload_netflix)
    assert result_netflix == "Netflix"
    
    # 5. Test matching an unknown layout (orthogonal vector)
    unknown_vec = [0.0] * 960
    unknown_vec[100] = 1.0
    payload_unknown = FingerprintPayload(
        device_id="TEST01",
        timestamp=datetime.utcnow(),
        visual_fps=[unknown_vec] * 10,
        audio_fp=[0.0] * 130
    )
    
    result_unknown = match_platform(session, payload_unknown)
    assert result_unknown == "unknown"
    
    session.close()
