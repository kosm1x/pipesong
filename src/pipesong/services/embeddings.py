"""Embedding service using sentence-transformers (multilingual-e5-small)."""
import logging
import time

from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None


def load_embedding_model(model_name: str = "intfloat/multilingual-e5-small") -> None:
    """Load embedding model into memory. Call once at startup."""
    global _model
    logger.info("Loading embedding model: %s", model_name)
    t0 = time.time()
    _model = SentenceTransformer(model_name)
    logger.info("Embedding model loaded in %.1fs (dim=%d)", time.time() - t0, _model.get_sentence_embedding_dimension())


def embed(text: str) -> list[float]:
    """Embed a single query text. Returns 384-dim vector.

    E5 models require 'query: ' prefix for queries.
    """
    if _model is None:
        raise RuntimeError("Embedding model not loaded. Call load_embedding_model() first.")
    vec = _model.encode(f"query: {text}", normalize_embeddings=True)
    return vec.tolist()


def embed_passage(text: str) -> list[float]:
    """Embed a document passage. Returns 384-dim vector.

    E5 models require 'passage: ' prefix for documents.
    """
    if _model is None:
        raise RuntimeError("Embedding model not loaded. Call load_embedding_model() first.")
    vec = _model.encode(f"passage: {text}", normalize_embeddings=True)
    return vec.tolist()


def embed_passages_batch(texts: list[str]) -> list[list[float]]:
    """Batch embed document passages. More efficient than calling embed_passage() in a loop."""
    if _model is None:
        raise RuntimeError("Embedding model not loaded. Call load_embedding_model() first.")
    prefixed = [f"passage: {t}" for t in texts]
    vecs = _model.encode(prefixed, normalize_embeddings=True, batch_size=32)
    return [v.tolist() for v in vecs]
