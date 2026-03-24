import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pipesong.models.agent import Agent
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
