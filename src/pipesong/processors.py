"""Custom Pipecat frame processors for Pipesong."""
import json
import random
import re
import time
import logging
from typing import Any

from pipecat.frames.frames import (
    EndFrame,
    LLMFullResponseEndFrame,
    LLMMessagesFrame,
    LLMTextFrame,
    TTSSpeakFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

from pipesong.models.call import Transcript
from pipesong.services.tools import ToolExecutor

logger = logging.getLogger(__name__)

# Match CJK and other non-Latin scripts
NON_LATIN_RE = re.compile(
    r'[\u0400-\u04FF'   # Cyrillic
    r'\u0600-\u06FF'    # Arabic
    r'\u0590-\u05FF'    # Hebrew
    r'\u0E00-\u0E7F'    # Thai
    r'\u2E80-\u9FFF'    # CJK Unified
    r'\uF900-\uFAFF'    # CJK Compatibility
    r'\uFE30-\uFE4F'    # CJK Compatibility Forms
    r'\uFF00-\uFFEF'    # Fullwidth Forms (Chinese punctuation)
    r'\u3000-\u303F'    # CJK Symbols and Punctuation
    r'\U00020000-\U0002A6DF'  # CJK Extension B
    r']+'
)

# Fix missing spaces from Qwen's tokenizer
MISSING_SPACE_BEFORE_INVERTED = re.compile(r'([a-záéíóúüñA-ZÁÉÍÓÚÜÑ.!?])([¿¡])')
MISSING_SPACE_AFTER_PUNCT = re.compile(r'([.!?,;:])([a-záéíóúüñA-ZÁÉÍÓÚÜÑ¿¡])')
CAMEL_CASE_SPLIT = re.compile(r'([a-záéíóúüñ])([A-ZÁÉÍÓÚÜÑ])')


class SpanishOnlyFilter(FrameProcessor):
    """Cleans LLM text for Spanish TTS.

    Use with TTS_AGGREGATION_MODE=sentence. This filter:
    1. Strips non-Latin characters (Qwen Chinese code-switching)
    2. Fixes missing spaces from Qwen's tokenizer
    3. Converts commas to periods so SENTENCE mode flushes at clause
       boundaries — giving Kokoro shorter, faster chunks while keeping
       enough context for proper Spanish phonemization.
    """

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame) and frame.text:
            text = frame.text

            # Strip non-Latin characters
            text = NON_LATIN_RE.sub(" ", text)

            # Voice-friendly: convert currency to spoken Spanish
            text = re.sub(r'\$\s*([\d,.]+)\s*MXN', r'\1 pesos', text)
            text = re.sub(r'\$\s*([\d,.]+)', r'\1 pesos', text)
            text = text.replace("MXN", "pesos")

            # Strip markdown/symbols that TTS reads literally
            text = re.sub(r'[*#&_~`|]', ' ', text)
            text = re.sub(r'^-\s+', '', text, flags=re.MULTILINE)
            text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)

            # Fix missing spaces
            text = MISSING_SPACE_BEFORE_INVERTED.sub(r'\1 \2', text)
            text = MISSING_SPACE_AFTER_PUNCT.sub(r'\1 \2', text)
            text = CAMEL_CASE_SPLIT.sub(r'\1 \2', text)

            # Convert commas to periods — tricks SENTENCE mode into flushing
            # at clause boundaries. Kokoro handles "phrase." the same as "phrase,"
            # but SENTENCE mode treats period as a flush point.
            text = text.replace(",", ".")

            # Collapse multiple spaces
            text = re.sub(r' +', ' ', text)

            if text.strip():
                await self.push_frame(LLMTextFrame(text=text), direction)
        else:
            await self.push_frame(frame, direction)


class TranscriptCapture(FrameProcessor):
    """Captures user transcriptions and assistant responses to PostgreSQL.

    Intercepts frames without consuming them — all frames pass through unchanged.
    Place between LLM and SpanishOnlyFilter to capture raw LLM text.
    """

    def __init__(self, call_id, session_factory, **kwargs):
        super().__init__(**kwargs)
        self._call_id = call_id
        self._session_factory = session_factory
        self._assistant_buffer = ""
        self._turn_start_ms = None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text:
            await self._save_transcript("user", frame.text.strip())

        elif isinstance(frame, LLMTextFrame) and frame.text:
            if not self._assistant_buffer:
                self._turn_start_ms = int(time.time() * 1000)
            self._assistant_buffer += frame.text

        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._assistant_buffer.strip():
                await self._save_transcript(
                    "assistant", self._assistant_buffer.strip(), self._turn_start_ms
                )
            self._assistant_buffer = ""
            self._turn_start_ms = None

        await self.push_frame(frame, direction)

    async def _save_transcript(self, role: str, content: str, timestamp_ms: int | None = None):
        try:
            async with self._session_factory() as session:
                session.add(Transcript(
                    call_id=self._call_id,
                    role=role,
                    content=content,
                    timestamp_ms=timestamp_ms or int(time.time() * 1000),
                ))
                await session.commit()
        except Exception as e:
            logger.error("TranscriptCapture: failed to save %s transcript: %s", role, e)


class RAGProcessor(FrameProcessor):
    """Retrieves relevant KB chunks on each user utterance and injects into LLM context.

    Place between TranscriptCapture(user) and user_aggregator. Intercepts
    TranscriptionFrame, embeds the query, runs pgvector cosine search,
    and appends top-K chunks as a system message in the LLM context.
    """

    def __init__(self, knowledge_base_id, session_factory, context, chunk_count=2, threshold=0.5, **kwargs):
        super().__init__(**kwargs)
        self._kb_id = knowledge_base_id
        self._session_factory = session_factory
        self._context = context
        self._chunk_count = chunk_count
        self._threshold = threshold
        self._last_rag_msg = None  # Track the RAG message object for replacement

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text and frame.text.strip():
            await self._retrieve_and_inject(frame.text.strip())

        await self.push_frame(frame, direction)

    async def _retrieve_and_inject(self, query: str):
        try:
            from pipesong.services.embeddings import embed
            from sqlalchemy import text as sql_text

            t0 = time.time()
            query_vec = embed(query)
            embed_ms = (time.time() - t0) * 1000

            async with self._session_factory() as session:
                # pgvector cosine distance: 1 - cosine_similarity
                # Use CAST() instead of :: to avoid SQLAlchemy parameter conflict
                result = await session.execute(
                    sql_text(
                        "SELECT content, 1 - (embedding <=> CAST(:vec AS vector)) AS similarity "
                        "FROM knowledge_base_chunks "
                        "WHERE knowledge_base_id = CAST(:kb_id AS uuid) "
                        "ORDER BY embedding <=> CAST(:vec AS vector) "
                        "LIMIT :limit"
                    ),
                    {"vec": str(query_vec), "kb_id": str(self._kb_id), "limit": self._chunk_count},
                )
                rows = result.fetchall()

            query_ms = (time.time() - t0) * 1000 - embed_ms
            total_ms = (time.time() - t0) * 1000

            # Filter by threshold
            chunks = [row[0] for row in rows if row[1] >= self._threshold]

            if chunks:
                # Sanitize chunks for voice readability
                clean_chunks = [self._sanitize_for_voice(c) for c in chunks]
                context_text = "\n\n".join(clean_chunks)
                rag_content = f"[KB] Usa esta información para responder. Sé breve y natural, como en una llamada telefónica:\n{context_text}"
                # Remove previous RAG message in-place, then append new one
                self._context._messages[:] = [
                    m for m in self._context._messages
                    if not (isinstance(m, dict) and str(m.get("content", "")).startswith("[KB] "))
                ]
                self._context.add_message({"role": "system", "content": rag_content})
                logger.info(
                    "RAG: query='%s' → %d chunks (embed=%.0fms, query=%.0fms, total=%.0fms)",
                    query[:50], len(chunks), embed_ms, query_ms, total_ms,
                )
            else:
                logger.debug("RAG: no relevant chunks for '%s' (threshold=%.2f)", query[:50], self._threshold)

        except Exception as e:
            logger.error("RAG retrieval failed: %s", e)

    @staticmethod
    def _sanitize_for_voice(text: str) -> str:
        """Clean KB chunk text for voice readability."""
        # Convert currency to spoken Spanish
        text = re.sub(r'\$\s*([\d,]+)\s*MXN', r'\1 pesos', text)
        text = re.sub(r'\$\s*([\d,]+)', r'\1 pesos', text)
        text = text.replace("MXN", "pesos mexicanos")
        # Remove thousand-separator commas in numbers
        text = re.sub(r'(\d),(\d{3})', r'\1\2', text)
        # Convert numbers 1000+ to spoken Mexican Spanish
        # so the LLM outputs them in spoken form and TTS pronounces correctly
        def _num_spoken(m):
            full = int(m.group(0))
            thousands = full // 1000
            remainder = full % 1000
            prefix = "mil" if thousands == 1 else f"{thousands} mil"
            return f"{prefix} {remainder}" if remainder else prefix
        text = re.sub(r'\b[1-9]\d{3,5}\b', _num_spoken, text)
        # Strip markdown formatting
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^\*\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^-\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*{2,}', '', text)
        text = re.sub(r'\*', '', text)
        text = re.sub(r'`[^`]*`', '', text)
        text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()


# Pattern for Qwen's native format: tool_name{"param": "value"} or tool_name({"param": "value"})
TOOL_CALL_NATIVE_RE = re.compile(r'(end_call|transfer_call|[a-z_]+)\s*\(?\s*(\{[^}]+\})\s*\)?')


def _extract_json_tool_call(text: str) -> dict | None:
    """Extract {"tool": ..., "arguments": ...} from text using progressive json.loads.

    Handles nested JSON in arguments (unlike regex). Scans for every '{' and tries
    to parse from that position.
    """
    for i, ch in enumerate(text):
        if ch == '{':
            # Try progressively longer substrings from this position
            for j in range(len(text), i, -1):
                candidate = text[i:j]
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict) and "tool" in parsed and "arguments" in parsed:
                        return parsed
                except (json.JSONDecodeError, ValueError):
                    continue
    return None

FILLER_PHRASES = [
    "Un momento, estoy verificando.",
    "Déjeme revisar eso por usted.",
    "Estoy consultando la información.",
    "Un segundo, por favor.",
]


class ToolCallProcessor(FrameProcessor):
    """Intercepts LLM output, detects JSON tool calls, executes them.

    Prompt-based approach for vLLM 0.6.6 (no native tool_choice support).
    Buffers LLM text tokens. On LLMFullResponseEndFrame:
    - If JSON tool call found: execute tool, inject result, trigger new LLM turn
    - If no tool call: replay buffered text downstream to TTS

    Place between LLM and TranscriptCapture(assistant) in the pipeline.
    """

    def __init__(
        self,
        tools: list[dict[str, Any]],
        tool_executor: ToolExecutor,
        context,  # LLMContext
        llm,  # OpenAILLMService
        variables: dict[str, Any] | None = None,
        call_control_id: str | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._tools = {t["name"]: t for t in tools}
        self._tool_executor = tool_executor
        self._context = context
        self._llm = llm
        self._variables = variables or {}
        self._call_control_id = call_control_id
        self._task = None  # set after task creation via set_task()
        self._buffer: list[LLMTextFrame] = []
        self._text_buffer = ""

    def set_task(self, task):
        self._task = task

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame):
            # Buffer tokens — don't pass through yet
            self._buffer.append(frame)
            self._text_buffer += frame.text or ""
            return  # swallow frame

        if isinstance(frame, LLMFullResponseEndFrame):
            text = self._text_buffer.strip()
            tool_call = self._parse_tool_call(text)

            if tool_call:
                tool_name = tool_call["tool"]
                arguments = tool_call.get("arguments", {})
                logger.info("Tool call detected: %s(%s)", tool_name, arguments)
                await self._execute_tool(tool_name, arguments)
                # Don't pass buffered frames or end frame — new LLM turn will generate fresh output
            else:
                # No tool call — fix numbers and replay as single frame
                fixed_text = self._fix_numbers_for_tts(text)
                if fixed_text != text:
                    # Emit fixed text as single frame instead of replaying originals
                    await self.push_frame(LLMTextFrame(text=fixed_text), direction)
                else:
                    for buffered_frame in self._buffer:
                        await self.push_frame(buffered_frame, direction)
                await self.push_frame(frame, direction)

            # Reset buffer
            self._buffer = []
            self._text_buffer = ""
            return

        # All other frames pass through unchanged
        await self.push_frame(frame, direction)

    @staticmethod
    def _fix_numbers_for_tts(text: str) -> str:
        """Convert numbers 1000+ to spoken Mexican Spanish for TTS pronunciation."""
        # Remove thousand-separator commas: 1,499 → 1499
        text = re.sub(r'(\d),(\d{3})', r'\1\2', text)
        # Convert to spoken form: 1499 → mil 499, 2500 → 2 mil 500
        def _num_spoken(m):
            full = int(m.group(0))
            thousands = full // 1000
            remainder = full % 1000
            prefix = "mil" if thousands == 1 else f"{thousands} mil"
            return f"{prefix} {remainder}" if remainder else prefix
        text = re.sub(r'\b[1-9]\d{3,5}\b', _num_spoken, text)
        return text

    def _parse_tool_call(self, text: str) -> dict | None:
        # Try structured JSON extraction first (handles nested arguments)
        result = _extract_json_tool_call(text)
        if result:
            return result

        # Fallback: Qwen's native format tool_name{"param": "value"}
        match = TOOL_CALL_NATIVE_RE.search(text)
        if match:
            tool_name = match.group(1)
            args_str = match.group(2)
            if tool_name in self._tools or tool_name in ("end_call", "transfer_call"):
                try:
                    arguments = json.loads(args_str)
                    logger.info("Parsed Qwen-native tool call: %s(%s)", tool_name, arguments)
                    return {"tool": tool_name, "arguments": arguments}
                except json.JSONDecodeError:
                    logger.warning("Native tool call JSON parse failed: %s", args_str)

        return None

    async def _execute_tool(self, tool_name: str, arguments: dict):
        # Built-in tools
        if tool_name == "end_call":
            await self._handle_end_call(arguments)
            return
        if tool_name == "transfer_call":
            await self._handle_transfer_call(arguments)
            return

        # HTTP tools
        tool_def = self._tools.get(tool_name)
        if not tool_def:
            logger.error("Unknown tool: %s", tool_name)
            await self._inject_tool_result(tool_name, {"error": f"Unknown tool: {tool_name}"})
            return

        # Play filler speech while tool executes
        filler = random.choice(FILLER_PHRASES)
        await self.push_frame(
            TTSSpeakFrame(text=filler, append_to_context=False),
            FrameDirection.DOWNSTREAM,
        )

        result = await self._tool_executor.execute(tool_def, arguments, self._variables)
        logger.info("Tool %s result: %s", tool_name, result)
        await self._inject_tool_result(tool_name, result)

    MAX_CONTEXT_MESSAGES = 20
    MAX_TOOL_RESULT_CHARS = 2000

    async def _inject_tool_result(self, tool_name: str, result: dict):
        """Inject tool result into LLM context and trigger a new completion."""
        # Sanitize: truncate large results, strip control characters
        result_text = json.dumps(result, ensure_ascii=False, default=str)
        if len(result_text) > self.MAX_TOOL_RESULT_CHARS:
            result_text = result_text[:self.MAX_TOOL_RESULT_CHARS] + "... (truncado)"

        self._context.add_message({
            "role": "assistant",
            "content": json.dumps({"tool": tool_name, "arguments": "..."}, ensure_ascii=False),
        })
        # Use "system" role for tool results to reduce prompt injection risk (H5)
        self._context.add_message({
            "role": "system",
            "content": f"[Resultado de herramienta {tool_name}]: {result_text}\nResponde al usuario con esta información.",
        })

        # Cap context growth (H4) — keep first message (system prompt) + last N
        msgs = self._context.get_messages()
        if len(msgs) > self.MAX_CONTEXT_MESSAGES:
            self._context.set_messages(msgs[:1] + msgs[-(self.MAX_CONTEXT_MESSAGES - 1):])

        # Trigger new LLM turn with updated context
        await self.push_frame(
            LLMMessagesFrame(self._context.get_messages()),
            FrameDirection.UPSTREAM,
        )

    async def _handle_end_call(self, arguments: dict):
        reason = arguments.get("reason", "Gracias por su llamada.")
        if self._task:
            await self._task.queue_frames([
                TTSSpeakFrame(text=reason, append_to_context=False),
                EndFrame(),
            ])
        else:
            logger.error("end_call: no task reference set")

    async def _handle_transfer_call(self, arguments: dict):
        target = arguments.get("target_number", "")
        if not target:
            logger.error("transfer_call: no target_number")
            return

        await self.push_frame(
            TTSSpeakFrame(text="Lo transfiero en este momento.", append_to_context=False),
            FrameDirection.DOWNSTREAM,
        )

        if self._call_control_id:
            try:
                import httpx
                from pipesong.config import settings

                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"https://api.telnyx.com/v2/calls/{self._call_control_id}/actions/transfer",
                        json={"to": target},
                        headers={
                            "Authorization": f"Bearer {settings.telnyx_api_key}",
                            "Content-Type": "application/json",
                        },
                    )
                logger.info("Call transferred to %s", target)
            except Exception as e:
                logger.error("transfer_call failed: %s", e)

        if self._task:
            await self._task.queue_frames([EndFrame()])
