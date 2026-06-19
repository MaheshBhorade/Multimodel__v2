import sys
import os
import numpy as np

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor

np.random.seed(42)
frame = np.random.randint(0, 256, (720, 1280, 3), dtype=np.uint8)
vis = DeepEmbeddingsExtractor.extract_visual(frame)
print("Type:", type(vis))
print("Len:", len(vis))
print("Non-zero count in first 256:", sum(1 for x in vis[:256] if abs(x) > 1e-10))
print("Non-zero count in remaining 704:", sum(1 for x in vis[256:] if abs(x) > 1e-10))
print("Sample first 10:", vis[:10])
print("Sample last 10:", vis[-10:])
