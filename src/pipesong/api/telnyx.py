"""Telnyx TeXML webhook — returns <Stream> XML to connect call audio to our WebSocket."""
from fastapi import APIRouter, Request
from fastapi.responses import Response

from pipesong.config import settings

router = APIRouter(tags=["telnyx"])


@router.post("/telnyx/webhook")
async def telnyx_webhook(request: Request):
    """Handle incoming Telnyx call. Returns TeXML that streams audio to our WebSocket."""
    if settings.app_public_url:
        # Use configured public URL (most reliable)
        ws_url = f"{settings.app_public_url}/ws"
    else:
        # Derive from request headers (works behind proxy)
        host = request.headers.get("x-forwarded-host", request.url.hostname)
        scheme = "wss" if request.url.scheme == "https" else "ws"
        ws_url = f"{scheme}://{host}:{settings.app_port}/ws"

    texml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Connect>
    <Stream url="{ws_url}" bidirectionalMode="rtp" />
  </Connect>
  <Pause length="600"/>
</Response>"""

    return Response(content=texml, media_type="application/xml")
