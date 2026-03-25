"""Document text extraction, chunking, and embedding for knowledge base ingestion."""
import csv
import io
import logging
from pathlib import Path

import tiktoken

from pipesong.services.embeddings import embed_passages_batch

logger = logging.getLogger(__name__)

_enc = tiktoken.get_encoding("cl100k_base")


def extract_text(file_bytes: bytes, filename: str) -> str:
    """Extract plain text from uploaded file bytes."""
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        import pymupdf4llm
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp.flush()
            return pymupdf4llm.to_markdown(tmp.name)

    if suffix == ".docx":
        from docx import Document
        doc = Document(io.BytesIO(file_bytes))
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    if suffix == ".html":
        from markdownify import markdownify
        return markdownify(file_bytes.decode("utf-8", errors="replace"))

    if suffix == ".csv":
        text = file_bytes.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        rows = []
        for row in reader:
            rows.append(" | ".join(f"{k}: {v}" for k, v in row.items()))
        return "\n".join(rows)

    if suffix in (".txt", ".md"):
        return file_bytes.decode("utf-8", errors="replace")

    raise ValueError(f"Unsupported file format: {suffix}")


def chunk_text(text: str, chunk_size: int = 512, overlap: int = 50) -> list[str]:
    """Split text into token-sized chunks with overlap."""
    tokens = _enc.encode(text)
    if not tokens:
        return []

    chunks = []
    step = max(chunk_size - overlap, 1)
    for start in range(0, len(tokens), step):
        chunk_tokens = tokens[start : start + chunk_size]
        chunk_text = _enc.decode(chunk_tokens)
        if chunk_text.strip():
            chunks.append(chunk_text.strip())
    return chunks


async def ingest_document(
    knowledge_base_id,
    file_bytes: bytes,
    filename: str,
    session,
    chunk_size: int = 512,
    chunk_overlap: int = 50,
) -> int:
    """Extract text, chunk, embed, and store in DB. Returns number of chunks created."""
    from pipesong.models.knowledge_base import KnowledgeBase, KnowledgeBaseChunk

    # Extract text
    text = extract_text(file_bytes, filename)
    if not text.strip():
        logger.warning("Empty text extracted from %s", filename)
        return 0

    # Chunk
    chunks = chunk_text(text, chunk_size, chunk_overlap)
    logger.info("Document %s: %d chars → %d chunks", filename, len(text), len(chunks))

    # Batch embed
    embeddings = embed_passages_batch(chunks)

    # Bulk insert
    for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
        session.add(KnowledgeBaseChunk(
            knowledge_base_id=knowledge_base_id,
            chunk_number=i,
            content=chunk,
            embedding=emb,
            source_document=filename,
        ))

    # Update KB stats
    kb = await session.get(KnowledgeBase, knowledge_base_id)
    if kb:
        kb.document_count += 1
        kb.chunk_count += len(chunks)
        kb.status = "ready"

    await session.commit()
    logger.info("Ingested %s: %d chunks stored", filename, len(chunks))
    return len(chunks)
