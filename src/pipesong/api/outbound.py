"""Outbound call initiation via Telnyx Call Control API.

Flow: POST /calls/outbound → Telnyx dials recipient → on answer, Telnyx sends
call.answered webhook → we issue stream_start to route audio to our WebSocket
→ same Pipecat pipeline as inbound calls.
"""
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from pipesong.config import settings
from pipesong.models.agent import Agent
from pipesong.models.call import Call
from pipesong.services.database import get_session

router = APIRouter(prefix="/calls", tags=["calls"])

# In-memory store for pending outbound calls (call_control_id → call metadata)
pending_outbound: dict[str, dict] = {}


class OutboundCallCreate(BaseModel):
    agent_id: uuid.UUID
    to_number: str
    variables: dict[str, Any] | None = None


class OutboundCallResponse(BaseModel):
    call_id: uuid.UUID
    status: str
    to_number: str
    from_number: str


@router.post("/outbound", response_model=OutboundCallResponse, status_code=201)
async def create_outbound_call(
    data: OutboundCallCreate,
    session: AsyncSession = Depends(get_session),
):
    agent = await session.get(Agent, data.agent_id)
    if not agent or not agent.is_active:
        raise HTTPException(404, "Agent not found or inactive")
    if not agent.phone_number:
        raise HTTPException(400, "Agent has no phone number configured (needed as caller ID)")
    if not settings.telnyx_connection_id:
        raise HTTPException(500, "TELNYX_CONNECTION_ID not configured")
    if not settings.app_public_url:
        raise HTTPException(500, "APP_PUBLIC_URL not configured (needed for WebSocket callback)")

    call_id = uuid.uuid4()
    call = Call(
        id=call_id,
        agent_id=agent.id,
        from_number=agent.phone_number,
        to_number=data.to_number,
        direction="outbound",
    )
    session.add(call)
    await session.commit()

    # Use Call Control API to initiate the call
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.telnyx.com/v2/calls",
                json={
                    "connection_id": settings.telnyx_connection_id,
                    "to": data.to_number,
                    "from": agent.phone_number,
                    # Don't set stream_url here — streaming_start is issued on call.answered
                    # to avoid duplicate WebSocket connections
                },
                headers={
                    "Authorization": f"Bearer {settings.telnyx_api_key}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code >= 400:
            raise HTTPException(502, f"Telnyx error: {resp.text}")

        resp_data = resp.json().get("data", {})
        call_control_id = resp_data.get("call_control_id", "")

        # Store pending outbound call info for webhook handler
        pending_outbound[call_control_id] = {
            "call_id": str(call_id),
            "agent_id": str(agent.id),
        }

    except httpx.HTTPError as e:
        raise HTTPException(502, f"Failed to initiate call: {e}")

    return OutboundCallResponse(
        call_id=call_id,
        status="initiated",
        to_number=data.to_number,
        from_number=agent.phone_number,
    )
