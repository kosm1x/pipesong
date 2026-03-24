"""Pipesong — Voice AI Engine

FastAPI application with Pipecat WebSocket pipeline for Telnyx phone calls.
"""
import asyncio
import io
import logging
import uuid
import wave
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket

from pipesong.config import settings
from pipesong.models.agent import Agent
from pipesong.models.call import Call
from pipesong.pipeline import create_pipeline
from pipesong.services.database import async_session, engine, init_db
from pipesong.services.storage import get_minio_client, upload_recording_async
from pipesong.services.webhooks import fire_webhook

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
from pipesong.api.outbound import router as outbound_router
from pipesong.api.telnyx import router as telnyx_router

app.include_router(agents_router)
app.include_router(calls_router)
app.include_router(outbound_router)
app.include_router(telnyx_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()

    # Outbound calls pass call_id and agent_id as query params
    query_call_id = websocket.query_params.get("call_id")
    query_agent_id = websocket.query_params.get("agent_id")
    call_id = uuid.UUID(query_call_id) if query_call_id else uuid.uuid4()
    is_outbound = bool(query_call_id)

    recorded_audio = {}
    agent_webhook_url = None
    agent_webhook_secret = None
    logger.info("WebSocket connected — call_id=%s outbound=%s", call_id, is_outbound)

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

            if is_outbound and query_agent_id:
                # Outbound: agent_id provided via query param, call record already exists
                agent = await session.get(Agent, uuid.UUID(query_agent_id))
            else:
                # Inbound: look up by phone number
                result = await session.execute(
                    select(Agent).where(Agent.phone_number == to_number, Agent.is_active == True)  # noqa: E712
                )
                agent = result.scalar_one_or_none()
                if not agent:
                    result = await session.execute(select(Agent).where(Agent.is_active == True).limit(1))  # noqa: E712
                    agent = result.scalar_one_or_none()
                    if agent:
                        logger.warning(
                            "Call %s: no agent for %s, falling back to '%s'",
                            call_id, to_number, agent.name,
                        )

            if not agent:
                logger.error("No agent found for call %s", call_id)
                await websocket.close()
                return

            if not is_outbound:
                call = Call(id=call_id, agent_id=agent.id, from_number=from_number, to_number=to_number)
                session.add(call)
                await session.commit()

            agent_prompt = agent.system_prompt
            agent_voice = agent.voice
            agent_language = agent.language
            agent_disclosure = agent.disclosure_message
            agent_tools = agent.tools or []
            agent_variables = agent.variables or {}
            agent_webhook_url = agent.webhook_url
            agent_webhook_secret = agent.webhook_secret

        # Fire call_started webhook
        if agent_webhook_url:
            asyncio.create_task(fire_webhook(
                agent_webhook_url, agent_webhook_secret, "call_started",
                {"call_id": str(call_id), "agent_id": str(agent.id),
                 "from_number": from_number, "to_number": to_number},
            ))

        # Variable substitution — merge agent variables with per-call context
        call_vars = {
            **agent_variables,
            "from_number": from_number,
            "to_number": to_number,
            "call_id": str(call_id),
        }
        for key, value in call_vars.items():
            agent_prompt = agent_prompt.replace(f"{{{{{key}}}}}", str(value))

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

        # Audio recording buffer
        from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

        audio_buffer = AudioBufferProcessor(user_continuous_stream=True)

        @audio_buffer.event_handler("on_audio_data")
        async def on_audio_data(buffer, audio, sample_rate, num_channels):
            recorded_audio["audio"] = audio
            recorded_audio["sample_rate"] = sample_rate
            recorded_audio["num_channels"] = num_channels
            logger.info("Call %s: audio captured (%d bytes, %dHz)", call_id, len(audio), sample_rate)

        await audio_buffer.start_recording()

        task, tool_processor = create_pipeline(
            transport=transport,
            system_prompt=agent_prompt,
            language=agent_language,
            voice=agent_voice,
            call_id=call_id,
            session_factory=async_session,
            audio_buffer=audio_buffer,
            tools=agent_tools if agent_tools else None,
            variables=call_vars,
            call_control_id=call_control_id,
        )
        if tool_processor:
            tool_processor.set_task(task)

        # Recording disclosure (legal requirement — Mexican telecom law)
        from pipecat.frames.frames import TTSSpeakFrame

        disclosure_text = agent_disclosure or settings.disclosure_text
        await task.queue_frame(TTSSpeakFrame(text=disclosure_text, append_to_context=False))

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

        # Upload recording to MinIO
        if recorded_audio.get("audio"):
            try:
                wav_io = io.BytesIO()
                with wave.open(wav_io, "wb") as wf:
                    wf.setnchannels(recorded_audio["num_channels"])
                    wf.setsampwidth(2)  # 16-bit PCM
                    wf.setframerate(recorded_audio["sample_rate"])
                    wf.writeframes(recorded_audio["audio"])
                wav_bytes = wav_io.getvalue()

                recording_url = await upload_recording_async(str(call_id), wav_bytes)
                logger.info("Call %s: recording uploaded (%d bytes)", call_id, len(wav_bytes))

                async with async_session() as session:
                    call = await session.get(Call, call_id)
                    if call:
                        call.recording_url = recording_url
                        await session.commit()
            except Exception as e:
                logger.error("Call %s: failed to upload recording — %s", call_id, e)

        # Fire call_ended webhook
        if agent_webhook_url:
            try:
                from pipesong.models.call import Transcript

                async with async_session() as session:
                    from sqlalchemy import select

                    result = await session.execute(
                        select(Transcript).where(Transcript.call_id == call_id).order_by(Transcript.created_at)
                    )
                    transcript_list = [{"role": t.role, "content": t.content} for t in result.scalars()]

                asyncio.create_task(fire_webhook(
                    agent_webhook_url, agent_webhook_secret, "call_ended",
                    {"call_id": str(call_id), "agent_id": str(agent.id),
                     "from_number": from_number, "to_number": to_number,
                     "duration_seconds": call.duration_seconds if call else None,
                     "transcript": transcript_list},
                ))
            except Exception as e:
                logger.error("Call %s: failed to fire call_ended webhook — %s", call_id, e)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("pipesong.main:app", host=settings.app_host, port=settings.app_port, reload=True)
