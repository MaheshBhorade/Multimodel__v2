import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent / "src"))

import json
import numpy as np
from content_platform.server.faiss_index import FAISSIndex

youtube_vec = [0.0] * 960
youtube_vec[5] = 1.0

netflix_vec = [0.0] * 960
netflix_vec[20] = 1.0

vis_idx = FAISSIndex(dim=960, gpu=False)
vis_idx.add(np.array(youtube_vec, dtype=np.float32).reshape(1, -1), [{"content_id": "youtube"}])
vis_idx.add(np.array(netflix_vec, dtype=np.float32).reshape(1, -1), [{"content_id": "netflix"}])

query = np.array(youtube_vec, dtype=np.float32).reshape(1, -1)
distances, ids = vis_idx.index.search(query, k=5)
print("Distances:", distances)
print("IDs:", ids)

results = vis_idx.search(query, k=5)
print("Results:", results)
