# Code Audit — Phase 3 RAG (2026-03-25)

Audited: Phase 3 files in `src/pipesong/`
Focus: reliability, speed, performance

## Summary

Phase 3 adds RAG with pgvector + multilingual-e5-small. Retrieval latency measured at 11-32ms on GPU. Core pipeline works — agent answers questions from uploaded documents. Several reliability and performance issues found.

**8 findings:** 3 Critical, 5 High — **all 8 resolved**

## All Findings

| #   | Category    | Severity     | File                      | Issue                                                                                                                                                           | Status                                                       |
| --- | ----------- | ------------ | ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| C1  | Performance | **Critical** | processors.py:174         | `embed()` is synchronous (sentence-transformers). Blocks event loop during live calls. Must wrap in `asyncio.to_thread()`.                                      | **RESOLVED** — wrapped in `asyncio.to_thread(embed, query)`  |
| C2  | Reliability | **Critical** | models/knowledge_base.py  | HNSW index not defined in SQLAlchemy model — only exists if manually created via SQL migration. Fresh installs via `create_all()` won't have the index.         | **RESOLVED** — `__table_args__` with HNSW index definition   |
| C3  | Reliability | **Critical** | services/ingestion.py:22  | `NamedTemporaryFile(delete=False)` for PDF extraction never cleaned up. Leaks temp files on disk.                                                               | **RESOLVED** — `os.unlink(tmp_path)` in finally block        |
| H1  | Performance | **High**     | services/embeddings.py:17 | `SentenceTransformer(model_name)` auto-detects GPU. Competes with vLLM for VRAM. Should explicitly set `device="cpu"` for production or validate VRAM headroom. | **RESOLVED** — explicit `device` param, default "cpu"        |
| H2  | Reliability | **High**     | processors.py:204         | `_context._messages[:]` direct internal mutation. Should use `get_messages()`/`set_messages()` public API. Fragile across Pipecat version upgrades.             | **RESOLVED** — uses `get_messages()`/`set_messages()`        |
| H3  | Reliability | **High**     | api/knowledge_base.py:86  | No `session.rollback()` before error-recovery status update. If ingestion fails mid-commit, the session is dirty and subsequent commit raises.                  | **RESOLVED** — `session.rollback()` before status update     |
| H4  | Security    | **High**     | api/knowledge_base.py:70  | No file size limit on document upload. Large files could OOM the server during extraction/embedding.                                                            | **RESOLVED** — 10 MB limit enforced                          |
| H5  | Reliability | **High**     | api/knowledge_base.py:16  | No validation bounds on `chunk_size`/`chunk_overlap` in KBCreate. User could set chunk_size=1 and create millions of chunks.                                    | **RESOLVED** — chunk_size 64-2048, overlap 0 to chunk_size-1 |
