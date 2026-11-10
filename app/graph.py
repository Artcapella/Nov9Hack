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
import math
import re
import time
from collections import Counter
from datetime import UTC, datetime

import numpy as np

from app import interactions

from app.people import DISCIPLINES, NEUTRAL, embedding_affinity, keyword_affinity, memory_vector, position_text, project
from app.policy import SYNTHETIC, visibility_filters, visible
from app.schemas import PublicProfile

logger = logging.getLogger(__name__)

NEIGHBOURS = 3
LIMIT = 1000
SPONSOR_ROLES = {"sponsor", "recruiter"}


STOPWORDS = {"and", "the", "for", "with", "of", "to", "in", "a", "an", "on", "at", "my", "our", "new", "ai"}


def _terms(doc: dict) -> Counter:
    """A card's most explicit features: what it offers (full weight) and seeks (half)."""
    terms: Counter = Counter()
    for field, weight in (("can_help_with", 1.0), ("looking_for", 0.5)):
        for tag in doc.get(field) or []:
            tag = re.sub(r"\s+", " ", tag.strip().lower())
            if tag and tag not in STOPWORDS and len(tag) > 1:
                terms[tag] += weight
    return terms


def cluster(unit: np.ndarray, docs: list[dict], coords: np.ndarray) -> tuple[list[int], list[dict]]:
    """Unlabelled k-means (cosine, k-means++ seeded deterministically) on the stored vectors,
    then each cluster is named after the features most over-represented among its members."""
    n = len(docs)
    if n < 4:
        return [0] * n, ([{"id": 0, "label": "everyone", "size": n, "features": [], "centroid2": {"x": 0.0, "y": 0.0}}] if n else [])
    k = int(np.clip(round(math.sqrt(n / 3)), 2, 9))
    rng = np.random.default_rng(7)
    centres = [unit[0]]
    for _ in range(1, k):
        dist = np.min([1 - unit @ c for c in centres], axis=0).clip(min=0) ** 2
        centres.append(unit[rng.choice(n, p=dist / dist.sum())] if dist.sum() > 0 else unit[rng.integers(n)])
    centres = np.array(centres)
    labels = np.zeros(n, dtype=int)
    for _ in range(30):
        new = np.argmax(unit @ centres.T, axis=1)
        if _ and np.array_equal(new, labels):
            break
        labels = new
        for c in range(k):
            members = unit[labels == c]
            if len(members):
                mean = members.mean(axis=0)
                centres[c] = mean / (np.linalg.norm(mean) or 1)
    doc_terms = [_terms(doc) for doc in docs]
    df: Counter = Counter()
    for terms in doc_terms:
        df.update(set(terms))
    order = sorted((c for c in range(k) if (labels == c).any()), key=lambda c: -(labels == c).sum())
    remap = {c: i for i, c in enumerate(order)}
    clusters, used = [], set()
    for c in order:
        idx = np.flatnonzero(labels == c)
        tf: Counter = Counter()
        present: Counter = Counter()
        for i in idx:
            tf.update(doc_terms[i])
            present.update(set(doc_terms[i]))
        # share within the cluster x lift over the whole room (an idf-like penalty for ubiquitous tags)
        scored = sorted((((present[t] / len(idx)) * math.log((n + 1) / (df[t] + 0.5)) * (1 + 0.1 * tf[t]), t) for t in present
                         if present[t] >= min(2, len(idx))), reverse=True)
        top = [t for _, t in scored if t not in used][:2] or [t for _, t in scored][:2]
        used.update(top)
        roles = Counter(role for i in idx for role in docs[i].get("roles") or [])
        label = " · ".join(top) if top else (roles.most_common(1)[0][0] + "s" if roles else f"group {len(clusters) + 1}")
        clusters.append({"id": remap[c], "label": label, "size": int(len(idx)),
                         "features": [{"term": t, "share": round(present[t] / len(idx), 2)} for _, t in scored[:5]],
                         "centroid2": {"x": round(float(coords[idx, 0].mean()), 4), "y": round(float(coords[idx, 1].mean()), 4)},
                         "centroid3": {"x": round(float(coords[idx, 0].mean()), 4), "y": round(float(coords[idx, 1].mean()), 4), "z": round(float(coords[idx, 2].mean()), 4)}})
    return [remap[int(c)] for c in labels], clusters


class GraphModel:
    def __init__(self, store):
        self.store = store
        self.cache: dict[tuple, dict] = {}
        self.axes: np.ndarray | None = None
        self.last_took_ms: float | None = None

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
            groups, clusters = cluster(unit, docs, coords) if len(docs) else ([], [])
            simulated = interactions.simulated_links(docs, similarity, SYNTHETIC) if len(docs) > 1 else []
            nodes = []
            for doc, xyz, colour, group in zip(docs, coords, colours, groups):
                nodes.append({**self.store.public_profile(doc), **colour, "cluster": group,
                              "position2": {"x": round(float(xyz[0]), 4), "y": round(float(xyz[1]), 4)},
                              "position3": {"x": round(float(xyz[0]), 4), "y": round(float(xyz[1]), 4), "z": round(float(xyz[2]), 4)}})
            cached = {"nodes": nodes, "similar": edges, "simulated": simulated, "clusters": clusters,
                      "stats": {"count": len(nodes), "dimensions": int(matrix.shape[1]) if len(matrix) else 0, "explained_variance": explained,
                                "engine": engine, "index": self.store.settings.index("user_vectors"),
                                "model_id": self.store.settings.elastic_inference_id if self.store.client else "memory demo · no inference",
                                "fingerprint": fingerprint, "neighbours": NEIGHBOURS,
                                "synthetic": sum(1 for doc in docs if doc.get("dataset") == SYNTHETIC)}}
            if len(self.cache) > 8:
                self.cache.clear()
            self.cache[key] = cached
        started = time.perf_counter()
        ids = {node["id"] for node in cached["nodes"]}
        real, activity = await interactions.links(self.store, viewer["id"], ids)
        self.last_took_ms = round((time.perf_counter() - started) * 1000, 1)
        # Edges exist only where people interacted. Similarity neighbours are kept separately (`similar`).
        edges = real + [link for link in cached["simulated"] if (link["a"], link["b"]) not in {(r["a"], r["b"]) for r in real}]
        out = {key: value for key, value in cached.items() if key != "simulated"}
        return {**out, "edges": edges, "activity": activity, "you": viewer["id"], "generated_at": datetime.now(UTC).isoformat(),
                "interactions": {"kinds": interactions.KINDS, "labels": interactions.LABELS, "real": len(real), "simulated": len(edges) - len(real)}}
