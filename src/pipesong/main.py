"""Pipesong — Voice AI Engine

FastAPI application with Pipecat WebSocket pipeline for Telnyx phone calls.
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket

from pipesong.api.agents import router as agents_router
from pipesong.api.calls import router as calls_router
from pipesong.api.telnyx import router as telnyx_router
from pipesong.config import settings
from pipesong.models.agent import Agent
from pipesong.models.call import Call, Transcript
from pipesong.pipeline import create_pipeline
from pipesong.services.database import async_session, init_db
from pipesong.services.storage import get_minio_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("pipesong")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing database...")
    await init_db()
    logger.info("Initializing MinIO...")
    get_minio_client()
    logger.info("Pipesong ready — listening on %s:%s", settings.app_host, settings.app_port)
    yield
    logger.info("Shutting down...")


app = FastAPI(title="Pipesong", version="0.1.0", lifespan=lifespan)
app.include_router(agents_router)
app.include_router(calls_router)
app.include_router(telnyx_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Handle Telnyx WebSocket media stream — run Pipecat pipeline per call."""
    await websocket.accept()
    call_id = uuid.uuid4()
    logger.info("WebSocket connected — call_id=%s", call_id)

    try:
        # Parse Telnyx metadata from first WebSocket message
        from pipecat.serializers.telnyx import TelnyxFrameSerializer
        from pipecat.transports.websocket.fastapi import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )
        from pipecat.vad.silero import SileroVADAnalyzer

        # Wait for Telnyx connected message with metadata
        first_msg = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        logger.info("Telnyx metadata: %s", {k: v for k, v in first_msg.items() if k != "event"})

        stream_id = first_msg.get("stream_id", "")
        call_control_id = first_msg.get("call_control_id", "")
        from_number = first_msg.get("from", "")
        to_number = first_msg.get("to", "")

        # Look up agent by phone number
        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(Agent).where(Agent.phone_number == to_number)
            )
            agent = result.scalar_one_or_none()

            if not agent:
                # Fall back to first agent if no number match
                result = await session.execute(select(Agent).limit(1))
                agent = result.scalar_one_or_none()

            if not agent:
                logger.error("No agent found for number %s", to_number)
                await websocket.close()
                return

            # Create call record
            call = Call(
                id=call_id,
                agent_id=agent.id,
                from_number=from_number,
                to_number=to_number,
            )
            session.add(call)
            await session.commit()

            agent_prompt = agent.system_prompt
            agent_voice = agent.voice
            agent_language = agent.language

        logger.info("Call %s: agent=%s from=%s to=%s", call_id, agent.name, from_number, to_number)

        # Create Telnyx transport
        serializer = TelnyxFrameSerializer(
            stream_id=stream_id,
            call_control_id=call_control_id,
            outbound_encoding="PCMU",
            inbound_encoding="PCMU",
            api_key=settings.telnyx_api_key,
        )

        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                serializer=serializer,
                audio_out_enabled=True,
                add_wav_header=False,
                vad_analyzer=SileroVADAnalyzer(),
            ),
        )

        # Create and run pipeline
        task = create_pipeline(
            transport=transport,
            system_prompt=agent_prompt,
            language=agent_language,
            voice=agent_voice,
        )

        logger.info("Call %s: pipeline starting", call_id)
        await task.run()
        logger.info("Call %s: pipeline ended", call_id)

    except asyncio.TimeoutError:
        logger.warning("Call %s: timeout waiting for Telnyx metadata", call_id)
    except Exception as e:
        logger.error("Call %s: error — %s", call_id, e, exc_info=True)
    finally:
        # Update call record
        try:
            async with async_session() as session:
                call = await session.get(Call, call_id)
                if call:
                    call.ended_at = datetime.now(timezone.utc)
                    call.status = "completed"
                    if call.started_at:
                        delta = call.ended_at - call.started_at
                        call.duration_seconds = int(delta.total_seconds())
                    await session.commit()
                    logger.info("Call %s: completed (%ss)", call_id, call.duration_seconds)
        except Exception as e:
            logger.error("Call %s: failed to update call record — %s", call_id, e)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "pipesong.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=True,
    )
