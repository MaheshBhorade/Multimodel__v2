import logging
import numpy as np

logger = logging.getLogger(__name__)


class SimpleVectorStore:

    def __init__(self):
        self.memory_visual = {}

    def upsert_reference(
        self,
        content_id: str,
        visual_vector: list[float],
        logo_vector: list[float]
    ):
        self.memory_visual[content_id] = visual_vector

    def delete_reference(self, content_id: str):
        if content_id in self.memory_visual:
            del self.memory_visual[content_id]

    def search_visual(
        self,
        vector: list[float],
        limit: int = 20
    ):
        if not vector:
            return []

        query = np.array(vector)

        candidates = []

        for cid, ref in self.memory_visual.items():

            if len(ref) != len(vector):
                continue

            ref_arr = np.array(ref)

            denom = (
                np.linalg.norm(query)
                * np.linalg.norm(ref_arr)
            )

            if denom == 0:
                continue

            score = float(
                np.dot(query, ref_arr) / denom
            )

            candidates.append(
                {
                    "content_id": cid,
                    "score": score
                }
            )

        candidates.sort(
            key=lambda x: x["score"],
            reverse=True
        )

        return candidates[:limit]

    def search_logo(
        self,
        vector: list[float],
        limit: int = 20
    ):
        return []


vector_store = SimpleVectorStore()
QdrantAdapter = SimpleVectorStore