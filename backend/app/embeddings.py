"""
Embedding model singleton.
Loaded once at startup via load_model(); shared by ScoringWorker and resume router.
encode() is async-safe — runs CPU-bound work in a thread pool.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_model = None


def load_model() -> None:
    """Load SentenceTransformer model. Call once during FastAPI lifespan startup."""
    global _model
    from sentence_transformers import SentenceTransformer

    model_name = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    logger.info("Loading embedding model: %s", model_name)
    _model = SentenceTransformer(model_name)
    logger.info("Embedding model ready: %s", model_name)


def is_loaded() -> bool:
    return _model is not None


def encode_sync(text: str) -> list[float]:
    """Encode text synchronously. Must not be called from the asyncio event loop."""
    if _model is None:
        raise RuntimeError("Embedding model not loaded")
    import numpy as np  # noqa: F401 — sentence-transformers already depends on numpy
    vec = _model.encode(text[:3000], normalize_embeddings=True)
    return vec.tolist()


async def encode(text: str) -> list[float]:
    """Encode text from an async context — delegates to thread pool to avoid blocking the loop."""
    return await asyncio.to_thread(encode_sync, text)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Dot product of two L2-normalised vectors == cosine similarity."""
    import numpy as np
    return float(np.dot(np.array(a), np.array(b)))
