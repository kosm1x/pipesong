import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pipesong.models.agent import Agent
from pipesong.models.call import Call, CallLatency
from pipesong.services.database import get_session

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentCreate(BaseModel):
    name: str
    system_prompt: str
    language: str = "es"
    voice: str = "em_alex"
    phone_number: str | None = None
    disclosure_message: str = "Esta llamada está siendo grabada para fines de calidad y entrenamiento."
    tools: list[dict[str, Any]] | None = None
    webhook_url: str | None = None
    webhook_secret: str | None = None
    variables: dict[str, Any] | None = None
    max_call_duration: int = 600
    is_active: bool = True
    knowledge_base_id: uuid.UUID | None = None
    kb_chunk_count: int = 3
    kb_similarity_threshold: float = 0.5
    vad_stop_secs: float | None = Field(default=None, ge=0.05, le=5.0)
    vad_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class AgentUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    language: str | None = None
    voice: str | None = None
    phone_number: str | None = None
    disclosure_message: str | None = None
    tools: list[dict[str, Any]] | None = None
    webhook_url: str | None = None
    webhook_secret: str | None = None
    variables: dict[str, Any] | None = None
    max_call_duration: int | None = None
    is_active: bool | None = None
    knowledge_base_id: uuid.UUID | None = None
    kb_chunk_count: int | None = None
    kb_similarity_threshold: float | None = None
    vad_stop_secs: float | None = Field(default=None, ge=0.05, le=5.0)
    vad_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    system_prompt: str
    language: str
    voice: str
    phone_number: str | None
    disclosure_message: str
    tools: list[dict[str, Any]] | None
    webhook_url: str | None
    variables: dict[str, Any] | None
    max_call_duration: int
    is_active: bool
    knowledge_base_id: uuid.UUID | None
    kb_chunk_count: int
    kb_similarity_threshold: float
    vad_stop_secs: float | None
    vad_confidence: float | None

    model_config = {"from_attributes": True}


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(data: AgentCreate, session: AsyncSession = Depends(get_session)):
    agent = Agent(**data.model_dump())
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    active_only: bool = True,
    session: AsyncSession = Depends(get_session),
):
    query = select(Agent).order_by(Agent.created_at.desc())
    if active_only:
        query = query.where(Agent.is_active == True)  # noqa: E712
    result = await session.execute(query)
    return result.scalars().all()


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    agent = await session.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: uuid.UUID,
    data: AgentUpdate,
    session: AsyncSession = Depends(get_session),
):
    agent = await session.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(agent, field, value)
    await session.commit()
    await session.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(agent_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    agent = await session.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    agent.is_active = False
    await session.commit()


# --- Latency aggregation (Phase 4a) ---


class PercentileStats(BaseModel):
    p50: float | None
    p90: float | None
    p95: float | None
    p99: float | None
    count: int


class AgentLatencyResponse(BaseModel):
    agent_id: uuid.UUID
    window_hours: int
    stt_ms: PercentileStats
    llm_ttft_ms: PercentileStats
    tts_ttfb_ms: PercentileStats
    e2e_ms: PercentileStats


def _percentiles(values: list[float]) -> PercentileStats:
    """Compute p50/p90/p95/p99 from a sorted list of values."""
    if not values:
        return PercentileStats(p50=None, p90=None, p95=None, p99=None, count=0)
    s = sorted(values)
    n = len(s)

    def _p(pct: float) -> float:
        idx = int(pct / 100 * (n - 1))
        return round(s[idx], 1)

    return PercentileStats(p50=_p(50), p90=_p(90), p95=_p(95), p99=_p(99), count=n)


@router.get("/{agent_id}/latency", response_model=AgentLatencyResponse)
async def get_agent_latency(
    agent_id: uuid.UUID,
    hours: int = Query(default=24, ge=1, le=720, description="Lookback window in hours"),
    session: AsyncSession = Depends(get_session),
):
    """p50/p90/p95/p99 latency aggregation for an agent over a time window."""
    agent = await session.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    result = await session.execute(
        select(CallLatency)
        .join(Call, CallLatency.call_id == Call.id)
        .where(Call.agent_id == agent_id, CallLatency.created_at >= since)
        .order_by(CallLatency.created_at)
    )
    rows = result.scalars().all()

    def _vals(field: str) -> list[float]:
        return [getattr(r, field) for r in rows if getattr(r, field) is not None]

    return AgentLatencyResponse(
        agent_id=agent_id,
        window_hours=hours,
        stt_ms=_percentiles(_vals("stt_ms")),
        llm_ttft_ms=_percentiles(_vals("llm_ttft_ms")),
        tts_ttfb_ms=_percentiles(_vals("tts_ttfb_ms")),
        e2e_ms=_percentiles(_vals("e2e_ms")),
    )
