"""Fire-and-forget webhook delivery with optional HMAC signing."""
import hashlib
import hmac
import json
import logging
import time

import httpx

logger = logging.getLogger(__name__)


async def fire_webhook(
    url: str,
    secret: str | None,
    event: str,
    payload: dict,
) -> None:
    """POST a webhook event. Fire-and-forget — logs errors, does not retry."""
    body = json.dumps(
        {"event": event, "timestamp": int(time.time()), **payload},
        ensure_ascii=False,
        default=str,
    )
    headers = {"Content-Type": "application/json"}
    if secret:
        sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()
        headers["X-Pipesong-Signature"] = sig

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(url, content=body, headers=headers)
        logger.info("Webhook %s → %s: %s", event, url, resp.status_code)
    except Exception as e:
        logger.error("Webhook %s → %s failed: %s", event, url, e)
