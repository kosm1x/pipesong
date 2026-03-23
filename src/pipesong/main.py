"""Pipesong — Voice AI Engine

FastAPI application with Pipecat WebSocket pipeline for Telnyx phone calls.
"""
import asyncio
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket

from pipesong.config import settings
from pipesong.models.agent import Agent
from pipesong.models.call import Call
from pipesong.pipeline import create_pipeline
from pipesong.services.database import async_session, engine, init_db
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
    await engine.dispose()
    logger.info("Database connections closed.")


app = FastAPI(title="Pipesong", version="0.1.0", lifespan=lifespan)

from pipesong.api.agents import router as agents_router
from pipesong.api.calls import router as calls_router
from pipesong.api.telnyx import router as telnyx_router

app.include_router(agents_router)
app.include_router(calls_router)
app.include_router(telnyx_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    call_id = uuid.uuid4()
    logger.info("WebSocket connected — call_id=%s", call_id)

    try:
        from pipecat.pipeline.runner import PipelineRunner
        from pipecat.runner.utils import parse_telephony_websocket
        from pipecat.serializers.telnyx import TelnyxFrameSerializer
        from pipecat.transports.websocket.fastapi import (
            FastAPIWebsocketParams,
            FastAPIWebsocketTransport,
        )

        transport_type, call_data = await parse_telephony_websocket(websocket)
        logger.info("Telnyx parsed: type=%s data=%s", transport_type, call_data)

        stream_id = call_data.get("stream_id", "")
        call_control_id = call_data.get("call_control_id", "")
        from_number = call_data.get("from", "")
        to_number = call_data.get("to", "")
        outbound_encoding = call_data.get("outbound_encoding", "PCMU")

        if not stream_id:
            logger.error("No stream_id from Telnyx")
            await websocket.close()
            return

        # Look up agent
        async with async_session() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(Agent).where(Agent.phone_number == to_number)
            )
            agent = result.scalar_one_or_none()
            if not agent:
                result = await session.execute(select(Agent).limit(1))
                agent = result.scalar_one_or_none()
                if agent:
                    logger.warning(
                        "Call %s: no agent for %s, falling back to '%s'",
                        call_id, to_number, agent.name,
                    )
            if not agent:
                logger.error("No agent found for %s and no fallback available", to_number)
                await websocket.close()
                return

            call = Call(id=call_id, agent_id=agent.id, from_number=from_number, to_number=to_number)
            session.add(call)
            await session.commit()

            agent_prompt = agent.system_prompt
            agent_voice = agent.voice
            agent_language = agent.language

        logger.info("Call %s: agent=%s from=%s to=%s", call_id, agent.name, from_number, to_number)

        serializer = TelnyxFrameSerializer(
            stream_id=stream_id,
            call_control_id=call_control_id,
            outbound_encoding=outbound_encoding or "PCMU",
            inbound_encoding="PCMU",
            api_key=settings.telnyx_api_key,
        )

        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                serializer=serializer,
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
            ),
        )

        task = create_pipeline(
            transport=transport,
            system_prompt=agent_prompt,
            language=agent_language,
            voice=agent_voice,
        )

        @transport.event_handler("on_client_disconnected")
        async def on_client_disconnected(transport, client):
            logger.info("Call %s: client disconnected", call_id)
            await task.cancel()

        runner = PipelineRunner()
        logger.info("Call %s: pipeline running", call_id)
        await runner.run(task)
        logger.info("Call %s: pipeline ended", call_id)

    except Exception as e:
        logger.error("Call %s: error — %s", call_id, e, exc_info=True)
    finally:
        try:
            async with async_session() as session:
                call = await session.get(Call, call_id)
                if call:
                    call.ended_at = datetime.now(timezone.utc)
                    call.status = "completed"
                    if call.started_at:
                        call.duration_seconds = int((call.ended_at - call.started_at).total_seconds())
                    await session.commit()
                    logger.info("Call %s: completed (%ss)", call_id, call.duration_seconds)
        except Exception as e:
            logger.error("Call %s: failed to update call — %s", call_id, e)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("pipesong.main:app", host=settings.app_host, port=settings.app_port, reload=True)
