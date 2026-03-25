"""Knowledge base CRUD + document upload endpoints."""
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pipesong.models.knowledge_base import KnowledgeBase, KnowledgeBaseChunk
from pipesong.services.database import get_session
from pipesong.services.ingestion import ingest_document

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


class KBCreate(BaseModel):
    name: str
    description: str | None = None
    chunk_size: int = 512
    chunk_overlap: int = 50

    def model_post_init(self, __context):
        if not (64 <= self.chunk_size <= 2048):
            raise ValueError("chunk_size must be between 64 and 2048")
        if not (0 <= self.chunk_overlap < self.chunk_size):
            raise ValueError("chunk_overlap must be >= 0 and < chunk_size")


class KBResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    status: str
    chunk_count: int
    document_count: int
    chunk_size: int
    chunk_overlap: int
    embedding_model: str

    model_config = {"from_attributes": True}


@router.post("", response_model=KBResponse, status_code=201)
async def create_knowledge_base(data: KBCreate, session: AsyncSession = Depends(get_session)):
    kb = KnowledgeBase(**data.model_dump())
    session.add(kb)
    await session.commit()
    await session.refresh(kb)
    return kb


@router.get("", response_model=list[KBResponse])
async def list_knowledge_bases(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc()))
    return result.scalars().all()


@router.get("/{kb_id}", response_model=KBResponse)
async def get_knowledge_base(kb_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    return kb


@router.post("/{kb_id}/documents")
async def upload_document(
    kb_id: uuid.UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")

    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(file_bytes) > MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=400, detail=f"File too large (max {MAX_UPLOAD_SIZE // 1024 // 1024} MB)")

    try:
        kb.status = "indexing"
        await session.commit()

        chunk_count = await ingest_document(
            knowledge_base_id=kb_id,
            file_bytes=file_bytes,
            filename=file.filename or "unknown",
            session=session,
            chunk_size=kb.chunk_size,
            chunk_overlap=kb.chunk_overlap,
        )
    except ValueError as e:
        await session.rollback()
        kb = await session.get(KnowledgeBase, kb_id)
        if kb:
            kb.status = "failed"
            await session.commit()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        await session.rollback()
        kb = await session.get(KnowledgeBase, kb_id)
        if kb:
            kb.status = "failed"
            await session.commit()
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}")

    return {"filename": file.filename, "chunks_created": chunk_count, "status": "ready"}


@router.delete("/{kb_id}", status_code=204)
async def delete_knowledge_base(kb_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    # Cascade deletes chunks via FK ondelete="CASCADE"
    await session.delete(kb)
    await session.commit()
