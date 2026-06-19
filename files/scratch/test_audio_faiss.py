import json
import numpy as np
from content_platform.server.db import SessionLocal
from content_platform.server.models import ContentSegment
from content_platform.server.matching import initialize_faiss_indexes, audio_index

with SessionLocal() as db:
    # Make sure FAISS indexes are loaded
    initialize_faiss_indexes(db)
    
    if audio_index is None:
        print("Audio index is None!")
        exit(1)
        
    print(f"Loaded audio index with {len(audio_index.id_to_content)} items. Dimension: {audio_index.dim}")
    
    # Get a few segments from DB
    segments = db.query(ContentSegment).limit(5).all()
    for s in segments:
        aud_fp = json.loads(s.audio_fp)
        print(f"\nTesting Segment ID: {s.segment_id} | Content ID: {s.content_id} | Offset: {s.segment_offset} | Len: {len(aud_fp)}")
        q_vec = np.array(aud_fp, dtype=np.float32)
        results = audio_index.search(q_vec, k=5)
        print("FAISS Search Results:")
        for r in results:
            print(f"  -> Content ID: {r['content_id']} | Offset: {r['segment_offset']} | Score: {r['score']:.4f}")
