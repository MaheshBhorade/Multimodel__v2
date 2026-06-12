import faiss
import numpy as np
import json
from pathlib import Path
import logging
import threading

logger = logging.getLogger(__name__)


class FAISSIndex:
    def __init__(self, dim: int, gpu: bool = True) -> None:
        self.dim = dim
        self.gpu = gpu
        self.lock = threading.Lock()
        self.index = faiss.IndexFlatL2(dim)
        
        if gpu:
            try:
                self.res = faiss.StandardGpuResources()
                self.index = faiss.index_cpu_to_gpu(self.res, 0, self.index)
                logger.info("FAISS GPU resources initialized.")
            except Exception as e:
                logger.warning(f"FAISS GPU initialization failed ({e}); using CPU index.")
                self.gpu = False
                
        self.id_to_content = []

    def add(self, vectors: np.ndarray, content_ids: list[str]) -> None:
        if vectors.dtype != np.float32:
            vectors = vectors.astype(np.float32)
            
        with self.lock:
            self.index.add(vectors)
            self.id_to_content.extend(content_ids)

    def search(self, query: np.ndarray, k: int = 5) -> list[tuple[str, float]]:
        if query.ndim == 1:
            query = query.reshape(1, -1)
            
        with self.lock:
            distances, ids = self.index.search(query.astype(np.float32), k)
            
        results = []
        for dist, idx in zip(distances[0], ids[0]):
            if idx < 0 or idx >= len(self.id_to_content):
                continue
            results.append((self.id_to_content[int(idx)], float(dist)))
            
        return results

    def save(self, index_path: str, meta_path: str) -> None:
        with self.lock:
            faiss.write_index(self.index, index_path)
            
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(self.id_to_content, f)

    @classmethod
    def load(cls, index_path: str, meta_path: str, dim: int, gpu: bool = True) -> "FAISSIndex":
        idx = faiss.read_index(index_path)
        
        if gpu:
            try:
                res = faiss.StandardGpuResources()
                idx = faiss.index_cpu_to_gpu(res, 0, idx)
                logger.info("FAISS GPU resources loaded.")
            except Exception as e:
                logger.warning(f"FAISS GPU load failed ({e}); using CPU index.")
                gpu = False
                
        instance = cls(dim=dim, gpu=gpu)
        instance.index = idx
        
        with open(meta_path, 'r', encoding='utf-8') as f:
            instance.id_to_content = json.load(f)
            
        return instance
