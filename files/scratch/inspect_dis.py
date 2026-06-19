import sys
import os
import dis

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from content_platform.fingerprint.embeddings import DeepEmbeddingsExtractor

print("Disassembling extract_visual:")
dis.dis(DeepEmbeddingsExtractor.extract_visual)
