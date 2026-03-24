import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pipesong.models.call import Call, Transcript
from pipesong.services.database import get_session

router = APIRouter(prefix="/calls", tags=["calls"])


class TranscriptResponse(BaseModel):
    role: str
    content: str
    timestamp_ms: int | None

    model_config = {"from_attributes": True}


class CallResponse(BaseModel):
    id: uuid.UUID
    agent_id: uuid.UUID
    from_number: str | None
    to_number: str | None
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    recording_url: str | None
    status: str

    model_config = {"from_attributes": True}


class CallDetailResponse(CallResponse):
    transcripts: list[TranscriptResponse] = []


@router.get("", response_model=list[CallResponse])
async def list_calls(
    agent_id: uuid.UUID | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
):
    query = select(Call).order_by(Call.started_at.desc()).limit(limit)
    if agent_id:
        query = query.where(Call.agent_id == agent_id)
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/{call_id}", response_model=CallDetailResponse)
async def get_call(call_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    call = await session.get(Call, call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    result = await session.execute(
        select(Transcript).where(Transcript.call_id == call_id).order_by(Transcript.created_at)
    )
    transcripts = result.scalars().all()
    return CallDetailResponse(
        **{c.key: getattr(call, c.key) for c in Call.__table__.columns},
        transcripts=[TranscriptResponse.model_validate(t) for t in transcripts],
    )
