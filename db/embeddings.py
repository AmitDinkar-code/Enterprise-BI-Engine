"""
Local deterministic embedding function for ChromaDB.

Why not the default embedding model?
  * Chroma's default downloads an ONNX sentence-transformer on first use. That
    is a network dependency at demo time and a ~80 MB artefact in the repo.
  * TR-013 requires the prototype to run from static local data with no live
    external dependency.
  * A hashed bag-of-words projection is fully deterministic, so `setup_db.py`
    is byte-reproducible and retrieval results never drift between runs.

The corpus is a small, lexically distinct evidence set (payment-gateway
language vs competitor-pricing language vs migration-latency language), which
this representation separates cleanly. The retrieval smoke test in setup_db.py
and tests/test_retrieval.py assert that separation rather than assuming it.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

DIM = 256

_TOKEN = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset("""
a an and are as at be been but by for from has have i if in is it its of on or
that the this to was were will with we you your our they their he she them then
than there here when what which who whom how all any can cad do does did not no
""".split())


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower())
            if t not in _STOPWORDS and len(t) > 2]


def _bucket(token: str) -> int:
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % DIM


def embed_text(text: str) -> list[float]:
    """Hashed bag-of-words with sublinear term frequency, L2 normalised."""
    vec = [0.0] * DIM
    counts = Counter(_tokenize(text))
    if not counts:
        return vec

    for token, count in counts.items():
        vec[_bucket(token)] += 1.0 + math.log(count)

    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0.0:
        vec = [v / norm for v in vec]
    return vec


class LocalDeterministicEmbedding:
    """Chroma-compatible embedding function. Stateless and offline."""

    def __call__(self, input):                    # noqa: A002 - Chroma's API name
        if isinstance(input, str):
            input = [input]
        return [embed_text(t) for t in input]

    # Chroma >= 1.5 dispatches through these instead of __call__.
    def embed_documents(self, input):             # noqa: A002
        return self(input)

    def embed_query(self, input):                 # noqa: A002
        return self(input)

    # ---- Chroma persistence hooks (present across 0.5.x / 1.x) ----
    @staticmethod
    def name() -> str:
        return "local_deterministic"

    def get_config(self) -> dict:
        return {"dim": DIM}

    @staticmethod
    def build_from_config(config: dict) -> "LocalDeterministicEmbedding":
        return LocalDeterministicEmbedding()

    def is_legacy(self) -> bool:
        return False

    @staticmethod
    def validate_config(config: dict) -> None:
        return None

    @staticmethod
    def validate_config_update(old_config: dict, new_config: dict) -> None:
        return None
