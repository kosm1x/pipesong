import uuid

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


class AgentResponse(BaseModel):
    id: uuid.UUID
    name: str
    system_prompt: str
    language: str
    voice: str
    phone_number: str | None
    disclosure_message: str

    model_config = {"from_attributes": True}


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(data: AgentCreate, session: AsyncSession = Depends(get_session)):
    agent = Agent(**data.model_dump())
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


@router.get("", response_model=list[AgentResponse])
async def list_agents(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Agent).order_by(Agent.created_at.desc()))
    return result.scalars().all()


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(agent_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    agent = await session.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent
