"""Telnyx webhook handler — supports both TeXML (inbound) and Call Control (outbound) events."""
import hashlib
import hmac
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response
from sqlalchemy import select

from pipesong.config import settings
from pipesong.models.call import Call
from pipesong.services.database import async_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["telnyx"])


@router.post("/telnyx/webhook")
async def telnyx_webhook(request: Request):
    """Handle Telnyx events.

    For inbound calls (TeXML): returns TeXML XML that streams audio to our WebSocket.
    For outbound calls (Call Control): handles call.answered by issuing stream_start.
    """
    # Verify webhook secret if configured
    if settings.telnyx_webhook_secret:
        token = request.query_params.get("token", "")
        if token != settings.telnyx_webhook_secret:
            logger.warning("Telnyx webhook rejected: invalid token")
            return {"error": "unauthorized"}, 401

    # TeXML inbound calls POST with empty/form body, not JSON
    try:
        body = await request.json()
    except Exception:
        return _handle_inbound_texml()

    # Call Control events have a "data" wrapper with "event_type"
    data = body.get("data", {})
    event_type = data.get("event_type", "")

    if event_type == "call.answered":
        return await _handle_call_answered(data)
    if event_type == "call.hangup":
        logger.info("Telnyx call.hangup: %s", data.get("payload", {}).get("call_control_id", "?"))
        return {"ok": True}
    if event_type:
        logger.debug("Telnyx event: %s", event_type)
        return {"ok": True}

    # No event_type → this is a TeXML inbound call
    return _handle_inbound_texml()


def _handle_inbound_texml() -> Response:
    """Return TeXML to stream inbound call audio to our WebSocket."""
    ws_url = f"{settings.app_public_url}/ws" if settings.app_public_url else "ws://localhost:8080/ws"

    texml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}" bidirectionalMode="rtp" />
  </Connect>
  <Pause length="600"/>
</Response>"""

    return Response(content=texml, media_type="application/xml")


async def _handle_call_answered(data: dict) -> dict:
    """When an outbound call is answered, start audio streaming to our WebSocket."""
    payload = data.get("payload", {})
    call_control_id = payload.get("call_control_id", "")

    # Look up outbound call from DB by call_control_id
    ws_url = f"{settings.app_public_url}/ws"
    try:
        async with async_session() as session:
            result = await session.execute(
                select(Call).where(Call.call_control_id == call_control_id)
            )
            call = result.scalar_one_or_none()
            if call:
                ws_url = f"{settings.app_public_url}/ws?call_id={call.id}&agent_id={call.agent_id}"
            else:
                logger.warning("Outbound call answered but no DB record for call_control_id=%s", call_control_id[:20])
    except Exception as e:
        logger.error("Failed to look up outbound call: %s", e)

    # Issue stream_start command via Call Control API
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/streaming_start",
                json={
                    "stream_url": ws_url,
                    "stream_track": "inbound_track",
                    "stream_bidirectional_mode": "rtp",
                },
                headers={
                    "Authorization": f"Bearer {settings.telnyx_api_key}",
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code >= 400:
            logger.error("stream_start failed for %s: HTTP %s — %s", call_control_id[:20], resp.status_code, resp.text)
        else:
            logger.info("stream_start for %s: %s", call_control_id[:20], resp.status_code)
    except Exception as e:
        logger.error("stream_start failed for %s: %s", call_control_id[:20], e)

    return {"ok": True}
