"""Telnyx webhook handler — supports both TeXML (inbound) and Call Control (outbound) events."""
import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

from pipesong.config import settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["telnyx"])


@router.post("/telnyx/webhook")
async def telnyx_webhook(request: Request):
    """Handle Telnyx events.

    For inbound calls (TeXML): returns TeXML XML that streams audio to our WebSocket.
    For outbound calls (Call Control): handles call.answered by issuing stream_start.
    """
    body = await request.json()

    # Call Control events have a "data" wrapper with "event_type"
    data = body.get("data", {})
    event_type = data.get("event_type", "")

    if event_type == "call.answered":
        return await _handle_call_answered(data)
    if event_type == "call.hangup":
        logger.info("Telnyx call.hangup: %s", data.get("payload", {}).get("call_control_id", "?"))
        return {"ok": True}
    if event_type:
        # Other Call Control events (call.initiated, etc.) — just ack
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

    # Look up pending outbound call to get the WebSocket URL with query params
    from pipesong.api.outbound import pending_outbound

    call_info = pending_outbound.pop(call_control_id, None)
    if call_info:
        ws_url = f"{settings.app_public_url}/ws?call_id={call_info['call_id']}&agent_id={call_info['agent_id']}"
    else:
        ws_url = f"{settings.app_public_url}/ws"
        logger.warning("Outbound call answered but no pending record for %s", call_control_id)

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
        logger.info("stream_start for %s: %s", call_control_id[:20], resp.status_code)
    except Exception as e:
        logger.error("stream_start failed for %s: %s", call_control_id[:20], e)

    return {"ok": True}
