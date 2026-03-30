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
    MetricsFrame,
    StartInterruptionFrame,
    TTSSpeakFrame,
    TranscriptionFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection

from pipesong.models.call import CallLatency, Transcript
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


class MetricsCollector(FrameProcessor):
    """Collects Pipecat's built-in TTFB metrics and persists per-turn latency to PostgreSQL.

    Intercepts MetricsFrame (emitted by STT/LLM/TTS services when enable_metrics=True)
    and accumulates per-turn TTFB data. On each LLMFullResponseEndFrame (end of assistant
    turn), flushes the accumulated metrics as a CallLatency row.

    Place at the end of the pipeline (after TTS, before transport output) to capture
    all metrics frames flowing through.
    """

    # Map Pipecat service class names to our column names
    _SERVICE_MAP = {
        "deepgram": "stt_ms",
        "stt": "stt_ms",
        "openai": "llm_ttft_ms",
        "llm": "llm_ttft_ms",
        "kokoro": "tts_ttfb_ms",
        "tts": "tts_ttfb_ms",
    }

    def __init__(self, call_id, session_factory, **kwargs):
        super().__init__(**kwargs)
        self._call_id = call_id
        self._session_factory = session_factory
        self._turn_index = 0
        self._current_turn: dict[str, float] = {}

    def _classify_metric(self, processor_name: str) -> str | None:
        """Map a Pipecat processor name like 'DeepgramSTTService' to a column name."""
        name_lower = processor_name.lower()
        for key, column in self._SERVICE_MAP.items():
            if key in name_lower:
                return column
        return None

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, MetricsFrame):
            for metric in frame.data:
                if isinstance(metric, TTFBMetricsData):
                    column = self._classify_metric(metric.processor)
                    if column:
                        # Pipecat reports TTFB in seconds, we store in ms
                        self._current_turn[column] = metric.value * 1000

        elif isinstance(frame, LLMFullResponseEndFrame):
            if self._current_turn:
                await self._flush_turn()

        await self.push_frame(frame, direction)

    async def _flush_turn(self):
        """Persist accumulated metrics for the current turn."""
        metrics = self._current_turn
        self._current_turn = {}

        # Sum of component TTFBs (approximation — excludes queue/network/buffering time)
        components = [metrics.get("stt_ms"), metrics.get("llm_ttft_ms"), metrics.get("tts_ttfb_ms")]
        available = [c for c in components if c is not None]
        e2e = sum(available) if available else None

        try:
            async with self._session_factory() as session:
                session.add(CallLatency(
                    call_id=self._call_id,
                    turn_index=self._turn_index,
                    stt_ms=metrics.get("stt_ms"),
                    llm_ttft_ms=metrics.get("llm_ttft_ms"),
                    tts_ttfb_ms=metrics.get("tts_ttfb_ms"),
                    e2e_ms=e2e,
                ))
                await session.commit()
            logger.info(
                "Latency turn %d: stt=%.0fms llm=%.0fms tts=%.0fms e2e=%.0fms",
                self._turn_index,
                metrics.get("stt_ms", 0),
                metrics.get("llm_ttft_ms", 0),
                metrics.get("tts_ttfb_ms", 0),
                e2e or 0,
            )
        except Exception as e:
            logger.error("MetricsCollector: failed to persist turn %d: %s", self._turn_index, e)

        self._turn_index += 1


# Spanish abbreviations that end with a period but aren't sentence boundaries
_ABBREVIATIONS = frozenset({
    "sr", "sra", "srta", "dr", "dra", "lic", "ing", "arq", "prof",
    "col", "av", "calle", "no", "núm", "tel", "ext", "dept", "edo",
    "mun", "cp", "etc", "approx", "vol", "cap", "pág", "fig",
    "min", "máx", "aprox", "vs",
})


class SentenceStreamBuffer(FrameProcessor):
    """Buffers LLM text tokens and emits complete sentences as TTSSpeakFrames.

    This is the core LLM↔TTS overlap mechanism: while Kokoro generates audio
    for sentence N, the LLM is already producing sentence N+1 into this buffer.

    Sentence boundaries: . ? ! (closing marks only — inverted ¿¡ appear at sentence starts).
    Excludes: abbreviations (Sr., Dra., etc.), ellipsis (...), decimal numbers.

    On interruption (StartInterruptionFrame): discards buffered partial sentence
    and clears any pending state. Pipecat's built-in interruption cancels the
    current TTS frame; this processor ensures the sentence queue is also cleared.

    Place between SpanishOnlyFilter and TTS in the pipeline.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._buffer = ""

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame) and frame.text:
            self._buffer += frame.text
            await self._flush_sentences(direction)
            return  # consumed — sentences emitted as TTSSpeakFrames

        if isinstance(frame, LLMFullResponseEndFrame):
            # Flush any remaining buffered text as final sentence
            if self._buffer.strip():
                await self.push_frame(
                    TTSSpeakFrame(text=self._buffer.strip()),
                    direction,
                )
                self._buffer = ""
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, StartInterruptionFrame):
            # Discard partial sentence on interruption
            if self._buffer:
                logger.debug("SentenceStreamBuffer: discarding %d chars on interruption", len(self._buffer))
                self._buffer = ""
            await self.push_frame(frame, direction)
            return

        # All other frames pass through
        await self.push_frame(frame, direction)

    async def _flush_sentences(self, direction: FrameDirection):
        """Extract and emit complete sentences from the buffer."""
        while True:
            boundary = self._find_sentence_boundary(self._buffer)
            if boundary < 0:
                break
            sentence = self._buffer[:boundary + 1].strip()
            self._buffer = self._buffer[boundary + 1:]
            if sentence:
                await self.push_frame(
                    TTSSpeakFrame(text=sentence),
                    direction,
                )

    @staticmethod
    def _find_sentence_boundary(text: str) -> int:
        """Find the index of the last char of the first complete sentence, or -1.

        Rules:
        - . ? ! are sentence enders
        - Skip abbreviations: if the word before . is in _ABBREVIATIONS, skip
        - Skip ellipsis: ... is not a boundary (but the 3rd dot is buffered)
        - Skip decimals: digit.digit is not a boundary
        """
        i = 0
        while i < len(text):
            ch = text[i]

            if ch in '?!':
                return i

            if ch == '.':
                # Ellipsis: skip ...
                if i + 2 < len(text) and text[i + 1] == '.' and text[i + 2] == '.':
                    i += 3
                    continue

                # Decimal number: digit.digit
                if i > 0 and i + 1 < len(text) and text[i - 1].isdigit() and text[i + 1].isdigit():
                    i += 1
                    continue

                # Abbreviation: word before period is in known set
                word_start = i - 1
                while word_start >= 0 and text[word_start].isalpha():
                    word_start -= 1
                word = text[word_start + 1:i].lower()
                if word in _ABBREVIATIONS:
                    i += 1
                    continue

                # Real sentence boundary
                return i

            i += 1

        return -1


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
            import asyncio
            from pipesong.services.embeddings import embed
            from sqlalchemy import text as sql_text

            t0 = time.time()
            query_vec = await asyncio.to_thread(embed, query)
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
                # Remove previous RAG message, then append new one
                current = self._context.get_messages()
                filtered = [m for m in current if not str(m.get("content", "")).startswith("[KB] ")]
                filtered.append({"role": "system", "content": rag_content})
                self._context.set_messages(filtered)
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
    """Extract {"tool": ..., "arguments": ...} from text using balanced-brace extraction.

    Finds the outermost {...} pair via brace counting, then json.loads once.
    Falls back to scanning up to 5 '{' positions to handle leading text.
    """
    max_scans = 5
    scans = 0
    for i, ch in enumerate(text):
        if ch == '{':
            scans += 1
            if scans > max_scans:
                break
            # Find matching closing brace via depth counting
            depth = 0
            for j in range(i, len(text)):
                if text[j] == '{':
                    depth += 1
                elif text[j] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[i:j + 1]
                        try:
                            parsed = json.loads(candidate)
                            if isinstance(parsed, dict) and "tool" in parsed and "arguments" in parsed:
                                return parsed
                        except (json.JSONDecodeError, ValueError):
                            pass
                        break
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

    Uses early bail-out heuristic for streaming compatibility:
    - First LLMTextFrame decides the mode for the entire turn
    - If text starts with { [ or a known tool name → BUFFER mode (full buffering)
    - Otherwise → STREAM mode (pass frames through immediately to SentenceStreamBuffer)
    - On LLMFullResponseEndFrame in buffer mode: check for tool call, execute or replay

    Place between LLM and TranscriptCapture(assistant) in the pipeline.
    """

    # Prefixes that indicate a tool call (not regular speech)
    _TOOL_PREFIXES = ('{', '[', '"tool', '{"')

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
        self._streaming = None  # None = undecided, True = stream, False = buffer

    def set_task(self, task):
        self._task = task

    def _looks_like_tool_call(self, text: str) -> bool:
        """Check if initial text looks like a tool call rather than speech."""
        stripped = text.lstrip()
        if not stripped:
            return False
        if any(stripped.startswith(p) for p in self._TOOL_PREFIXES):
            return True
        # Check for Qwen native format: tool_name{ or tool_name(
        for tool_name in self._tools:
            if stripped.startswith(tool_name):
                return True
        if stripped.startswith("end_call") or stripped.startswith("transfer_call"):
            return True
        return False

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMTextFrame):
            text = frame.text or ""
            self._text_buffer += text

            # Decide mode once we have non-whitespace content
            if self._streaming is None:
                if not self._text_buffer.strip():
                    return  # Wait for non-whitespace before deciding
                if self._looks_like_tool_call(self._text_buffer):
                    self._streaming = False
                    logger.debug("ToolCallProcessor: buffer mode (tool call pattern)")
                else:
                    self._streaming = True
                    logger.debug("ToolCallProcessor: stream mode (speech)")

            if self._streaming:
                # Fix numbers inline and pass through immediately
                fixed = self._fix_numbers_for_tts(text)
                await self.push_frame(LLMTextFrame(text=fixed), direction)
            else:
                # Buffer for tool call detection
                self._buffer.append(frame)
            return

        if isinstance(frame, LLMFullResponseEndFrame):
            if self._streaming is False:
                # Buffer mode: check for tool call
                text = self._text_buffer.strip()
                tool_call = self._parse_tool_call(text)

                if tool_call:
                    tool_name = tool_call["tool"]
                    arguments = tool_call.get("arguments", {})
                    logger.info("Tool call detected: %s(%s)", tool_name, arguments)
                    await self._execute_tool(tool_name, arguments)
                else:
                    # False positive — not actually a tool call, replay as speech
                    fixed_text = self._fix_numbers_for_tts(text)
                    await self.push_frame(LLMTextFrame(text=fixed_text), direction)
                    await self.push_frame(frame, direction)
            else:
                # Stream mode: just pass through the end frame
                await self.push_frame(frame, direction)

            # Reset for next turn
            self._buffer = []
            self._text_buffer = ""
            self._streaming = None
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
