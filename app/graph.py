"""Live network graph: the discoverable cards in Elastic, placed by their stored embeddings.

Elastic mode reads each card's stored `profile_semantic` Jina vectors straight from the
index (`_inference_fields`, chunk vectors averaged) — nothing is re-embedded. The 3-D
position is a PCA projection of those vectors; edges are each card's nearest neighbours by
cosine in the full-dimensional space, so an edge means "close in the original space", not
"close on screen". The 2-D position/colour is the same discipline-anchor projection the
People view uses. Axis signs are kept consistent between refreshes so the layout does not
flip when someone joins. Memory mode uses the transparent concept/term vectors instead and
labels itself.

Only cards that pass the visibility policy are read, and only public card fields leave.
"""
import hashlib
import logging
from datetime import UTC, datetime

import numpy as np

from app.people import DISCIPLINES, NEUTRAL, embedding_affinity, keyword_affinity, memory_vector, position_text, project
from app.policy import SYNTHETIC, visibility_filters, visible
from app.schemas import PublicProfile

logger = logging.getLogger(__name__)

NEIGHBOURS = 3
LIMIT = 1000
SPONSOR_ROLES = {"sponsor", "recruiter"}


class GraphModel:
    def __init__(self, store):
        self.store = store
        self.cache: dict[tuple, dict] = {}
        self.axes: np.ndarray | None = None

    def _keep(self, doc: dict, include_synthetic: bool, include_sponsors: bool) -> bool:
        return (visible(doc, self.store.settings.demo_event_id)
                and (include_synthetic or doc.get("dataset") != SYNTHETIC)
                and (include_sponsors or not SPONSOR_ROLES.intersection(doc.get("roles", []))))

    def _filters(self, include_synthetic: bool, include_sponsors: bool) -> list[dict]:
        filters = visibility_filters(self.store.settings.demo_event_id)
        if not include_synthetic:
            filters.append({"bool": {"must_not": [{"term": {"dataset": SYNTHETIC}}]}})
        if not include_sponsors:
            filters.append({"bool": {"must_not": [{"terms": {"roles": sorted(SPONSOR_ROLES)}}]}})
        return filters

    async def _fingerprint(self, include_synthetic: bool, include_sponsors: bool) -> tuple[str, int]:
        store = self.store
        if store.client:
            response = await store.client.search(index=store.settings.index("user_vectors"), size=LIMIT, source=["id", "approval_version", "created_at"],
                                                 query={"bool": {"filter": self._filters(include_synthetic, include_sponsors)}}, sort=[{"id": "asc"}])
            rows = [hit["_source"] for hit in response["hits"]["hits"]]
        else:
            rows = sorted((doc for doc in store.memory_profiles.values() if self._keep(doc, include_synthetic, include_sponsors)), key=lambda d: d["id"])
        digest = hashlib.sha256("|".join(f"{r['id']}:{r.get('approval_version')}:{r.get('created_at')}" for r in rows).encode()).hexdigest()[:16]
        return digest, len(rows)

    async def _documents(self, include_synthetic: bool, include_sponsors: bool) -> tuple[list[dict], np.ndarray, str]:
        store = self.store
        fields = list(PublicProfile.model_fields) + ["event_id", "consent_to_discover", "state"]
        if store.client:
            response = await store.client.search(
                index=store.settings.index("user_vectors"), size=LIMIT, sort=[{"id": "asc"}],
                source={"includes": fields + ["_inference_fields.profile_semantic"], "exclude_vectors": False},
                query={"bool": {"filter": self._filters(include_synthetic, include_sponsors)}})
            docs, vectors = [], []
            for hit in response["hits"]["hits"]:
                source = hit["_source"]
                chunks = (source.pop("_inference_fields", {}).get("profile_semantic", {}).get("inference", {})
                          .get("chunks", {}).get("profile_semantic", []))
                if not chunks or not self._keep(source, include_synthetic, include_sponsors):
                    continue
                docs.append(source)
                vectors.append(np.mean([chunk["embeddings"] for chunk in chunks], axis=0))
            return docs, np.array(vectors, dtype=float), "jina_stored"
        docs = sorted((doc for doc in store.memory_profiles.values() if self._keep(doc, include_synthetic, include_sponsors)), key=lambda d: d["id"])
        vocab: dict[str, int] = {}
        sparse = [memory_vector(position_text(doc) + " " + doc.get("public_summary", "")) for doc in docs]
        for vector in sparse:
            for key in vector:
                vocab.setdefault(key, len(vocab))
        matrix = np.zeros((len(docs), max(1, len(vocab))))
        for row, vector in enumerate(sparse):
            for key, value in vector.items():
                matrix[row, vocab[key]] = value
        return docs, matrix, "memory_concepts"

    def _project(self, matrix: np.ndarray) -> tuple[np.ndarray, list[float]]:
        n = len(matrix)
        if n == 0:
            return np.zeros((0, 3)), [0.0, 0.0, 0.0]
        centred = matrix - matrix.mean(axis=0)
        if n < 2 or not np.any(centred):
            return np.zeros((n, 3)), [0.0, 0.0, 0.0]
        _, singular, vt = np.linalg.svd(centred, full_matrices=False)
        k = min(3, vt.shape[0])
        axes = vt[:k]
        if self.axes is not None and self.axes.shape[1] == axes.shape[1]:
            for i in range(min(k, len(self.axes))):
                if float(axes[i] @ self.axes[i]) < 0:
                    axes[i] = -axes[i]
        else:
            for i in range(k):
                if axes[i][np.argmax(np.abs(axes[i]))] < 0:
                    axes[i] = -axes[i]
        self.axes = axes
        coords = centred @ axes.T
        if k < 3:
            coords = np.hstack([coords, np.zeros((n, 3 - k))])
        scale = float(np.percentile(np.linalg.norm(coords, axis=1), 90)) or 1.0
        variance = singular ** 2
        explained = [round(float(v / variance.sum()), 4) for v in variance[:3]] + [0.0] * (3 - k)
        return coords / scale, explained

    async def _colours(self, docs: list[dict], matrix: np.ndarray, engine: str) -> list[dict]:
        if engine == "jina_stored":
            try:
                space = self.store.space
                if space.anchors is None:
                    space.anchors = await space._embed([d["anchor"] for d in DISCIPLINES])
                return [project(embedding_affinity(list(vector), space.anchors)) for vector in matrix]
            except Exception:
                logger.warning("Anchor inference unavailable; colouring the graph by keyword affinity")
        return [project(keyword_affinity(position_text(doc)) if position_text(doc) else NEUTRAL) for doc in docs]

    async def build(self, viewer: dict, include_synthetic: bool = True, include_sponsors: bool = False) -> dict:
        fingerprint, count = await self._fingerprint(include_synthetic, include_sponsors)
        key = (fingerprint, include_synthetic, include_sponsors)
        cached = self.cache.get(key)
        if not cached:
            docs, matrix, engine = await self._documents(include_synthetic, include_sponsors)
            coords, explained = self._project(matrix)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True) if len(matrix) else np.zeros((0, 1))
            unit = matrix / np.where(norms == 0, 1, norms) if len(matrix) else matrix
            similarity = unit @ unit.T if len(matrix) else np.zeros((0, 0))
            edges, seen = [], set()
            for i in range(len(docs)):
                similarity[i, i] = -np.inf
                for j in np.argsort(-similarity[i])[:NEIGHBOURS]:
                    if not np.isfinite(similarity[i, j]):
                        continue
                    pair = (min(i, int(j)), max(i, int(j)))
                    if pair not in seen:
                        seen.add(pair)
                        edges.append({"a": docs[pair[0]]["id"], "b": docs[pair[1]]["id"], "similarity": round(float(similarity[i, j]), 4)})
            colours = await self._colours(docs, matrix, engine)
            nodes = []
            for doc, xyz, colour in zip(docs, coords, colours):
                nodes.append({**self.store.public_profile(doc), **colour,
                              "position3": {"x": round(float(xyz[0]), 4), "y": round(float(xyz[1]), 4), "z": round(float(xyz[2]), 4)}})
            cached = {"nodes": nodes, "edges": edges,
                      "stats": {"count": len(nodes), "dimensions": int(matrix.shape[1]) if len(matrix) else 0, "explained_variance": explained,
                                "engine": engine, "index": self.store.settings.index("user_vectors"),
                                "model_id": self.store.settings.elastic_inference_id if self.store.client else "memory demo · no inference",
                                "fingerprint": fingerprint, "neighbours": NEIGHBOURS,
                                "synthetic": sum(1 for doc in docs if doc.get("dataset") == SYNTHETIC)}}
            if len(self.cache) > 8:
                self.cache.clear()
            self.cache[key] = cached
        return {**cached, "you": viewer["id"], "generated_at": datetime.now(UTC).isoformat()}
