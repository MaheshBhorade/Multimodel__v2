import json
import traceback
import numpy as np
from content_platform.server.db import SessionLocal
from content_platform.server.models import ContentSegment
from content_platform.server.matching import (
    FAISS_VISUAL_DIM, FAISS_AUDIO_DIM, VISUAL_INDEX_PATH, VISUAL_META_PATH,
    AUDIO_INDEX_PATH, AUDIO_META_PATH, FAISS_INDEX_DIR
)
from content_platform.server.faiss_index import FAISSIndex

with SessionLocal() as db:
    db_count = db.query(ContentSegment).count()
    print(f"Total ContentSegment records in DB: {db_count}")
    
    print("Checking exists:")
    print(f"  VISUAL_INDEX_PATH exists: {VISUAL_INDEX_PATH.exists()}")
    print(f"  VISUAL_META_PATH exists: {VISUAL_META_PATH.exists()}")
    print(f"  AUDIO_INDEX_PATH exists: {AUDIO_INDEX_PATH.exists()}")
    print(f"  AUDIO_META_PATH exists: {AUDIO_META_PATH.exists()}")
    
    try:
        vis_idx = FAISSIndex(dim=FAISS_VISUAL_DIM, gpu=True)
        aud_idx = FAISSIndex(dim=FAISS_AUDIO_DIM, gpu=True)
        
        items = db.query(ContentSegment).all()
        print(f"Loaded {len(items)} segments from DB.")
        
        added_vis = 0
        added_aud = 0
        
        for item in items:
            vis_fp = json.loads(item.visual_fp)
            aud_fp = json.loads(item.audio_fp)
            
            meta = {
                "content_id": item.content_id,
                "segment_index": item.segment_index,
                "segment_offset": item.segment_offset
            }
            if len(vis_fp) == FAISS_VISUAL_DIM:
                vis_idx.add(np.array(vis_fp, dtype=np.float32).reshape(1, -1), [meta])
                added_vis += 1
            if len(aud_fp) == FAISS_AUDIO_DIM:
                aud_idx.add(np.array(aud_fp, dtype=np.float32).reshape(1, -1), [meta])
                added_aud += 1
                
        print(f"Added to visual: {added_vis}, Added to audio: {added_aud}")
        
        print("Saving...")
        vis_idx.save(str(VISUAL_INDEX_PATH), str(VISUAL_META_PATH))
        aud_idx.save(str(AUDIO_INDEX_PATH), str(AUDIO_META_PATH))
        print("Saved successfully!")
        
    except Exception as e:
        print(f"Exception occurred:")
        traceback.print_exc()
