"""MCP tools over the Pipesong REST API (roadmap B.1).

Each tool is a plain async function: typed params in, the REST JSON out. Names,
types and bounds mirror the Pydantic models in `pipesong.api.agents` and
`pipesong.api.outbound`; the REST layer stays the validator of record (its 422
body reaches the caller verbatim). `server.py` registers these; tests call them
directly against an `httpx.MockTransport`.
"""
import uuid
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from pipesong.mcp.client import PipesongAPIError, PipesongClient

_client = PipesongClient()

# --- Parameter types (shared by agent_create / agent_update) ---

AgentId = Annotated[uuid.UUID, Field(description="Agent id (UUID) as returned by agent_create or agent_list.")]
CallId = Annotated[uuid.UUID, Field(description="Call id (UUID) as returned by call_start.")]
_NAME = "Display name, max 255 chars."
_SYSTEM_PROMPT = (
    "LLM system prompt. `{{key}}` placeholders are filled from `variables` plus "
    "from_number, to_number and call_id at call time."
)
Name = Annotated[str | None, Field(description=_NAME)]
SystemPrompt = Annotated[str | None, Field(description=_SYSTEM_PROMPT)]
Language = Annotated[str | None, Field(description='Language code, max 5 chars. Server default "es".')]
Voice = Annotated[
    str | None,
    Field(description='Kokoro TTS voice id used for this agent\'s calls, e.g. "em_alex" (server default).'),
]
PhoneNumber = Annotated[
    str | None,
    Field(description="E.164 number, max 20 chars, e.g. +5215512345678. Routes inbound calls to this agent "
          "and is the caller ID for call_start (required before call_start)."),
]
DisclosureMessage = Annotated[
    str | None,
    Field(description="Recording disclosure spoken at the start of every call. Server default is a Spanish "
          "recording notice."),
]
Tools = Annotated[
    list[dict[str, Any]] | None,
    Field(description=(
        "HTTP tools the agent may call mid-conversation. Each item: {\"name\": str (required), "
        "\"description\": str, \"parameters\": {\"properties\": {<param>: {\"description\": str}}, "
        "\"required\": [<param>, ...]}, \"endpoint\": URL (required; {{var}} filled from variables, "
        "{param} from the call's arguments), \"method\": \"GET\"|\"POST\"|\"PUT\"|\"DELETE\" (default "
        "POST; POST/PUT send the arguments as the JSON body), \"headers\": {str: str} ({{var}} filled), "
        "\"timeout_seconds\": number (default 10)}. Parsed by services/tools.py (ToolExecutor, "
        "format_tools_prompt). end_call and transfer_call are built in; do not redefine them."
    )),
]
WebhookUrl = Annotated[
    str | None,
    Field(description="URL that receives call_started / call_ended events, max 500 chars."),
]
WebhookSecret = Annotated[
    str | None,
    Field(description="Shared secret used to sign webhook payloads. Write-only: never returned in responses."),
]
Variables = Annotated[
    dict[str, Any] | None,
    Field(description="Values substituted into `{{key}}` placeholders of the system prompt on every call."),
]
MaxCallDuration = Annotated[
    int | None, Field(description="Hard cap on call length in SECONDS. Server default 600.")
]
IsActive = Annotated[
    bool | None,
    Field(description="Inactive agents take no inbound calls and call_start refuses them. Server default true."),
]
KnowledgeBaseId = Annotated[
    uuid.UUID | None, Field(description="Knowledge base (UUID) used for retrieval during calls.")
]
KbChunkCount = Annotated[
    int | None, Field(description="Knowledge-base chunks retrieved per user turn. Server default 3.")
]
KbSimilarityThreshold = Annotated[
    float | None, Field(description="Minimum similarity for a retrieved chunk to be used. Server default 0.5.")
]
EotThreshold = Annotated[
    float | None,
    Field(ge=0.5, le=0.9, description="Deepgram Flux end-of-turn confidence, 0.5-0.9. Unset = Flux default 0.7. "
          "Higher waits for more certainty that the caller finished."),
]
EagerEotThreshold = Annotated[
    float | None,
    Field(ge=0.3, le=0.9, description="Flux eager end-of-turn confidence, 0.3-0.9; must be <= the effective "
          "eot_threshold (0.7 when that is unset) or the API returns 422. Unset = eager OFF. Each eager "
          "turn costs an extra LLM call."),
]
EotTimeoutMs = Annotated[
    int | None,
    Field(ge=500, le=10000, description="Flux end-of-turn silence timeout in MILLISECONDS, 500-10000. "
          "Unset = Flux default 5000."),
]

# Columns that are nullable on the agent record, so a PATCH may set them back to null.
ClearableField = Literal[
    "phone_number", "tools", "webhook_url", "webhook_secret", "variables",
    "knowledge_base_id", "eot_threshold", "eager_eot_threshold", "eot_timeout_ms",
]


def _body(**fields: Any) -> dict[str, Any]:
    """Keep only the fields the caller set; UUIDs become strings for JSON."""
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in fields.items() if v is not None}


# --- Tools ---


async def health() -> dict[str, Any]:
    """Check that the Pipesong API is up. Cheapest liveness probe; call it first.

    Returns {"status": "ok"} from GET /health. Errors when the API is unreachable
    (wrong PIPESONG_API_URL or server down) or answers non-2xx. /health is exempt
    from API-key auth, so a pass does NOT validate PIPESONG_API_KEY: an HTTP 401 on
    any other tool means that key is wrong or unset.
    """
    return await _client.request("GET", "/health")


async def agent_create(
    name: Annotated[str, Field(description=_NAME)],
    system_prompt: Annotated[str, Field(description=_SYSTEM_PROMPT)],
    language: Language = None,
    voice: Voice = None,
    phone_number: PhoneNumber = None,
    disclosure_message: DisclosureMessage = None,
    tools: Tools = None,
    webhook_url: WebhookUrl = None,
    webhook_secret: WebhookSecret = None,
    variables: Variables = None,
    max_call_duration: MaxCallDuration = None,
    is_active: IsActive = None,
    knowledge_base_id: KnowledgeBaseId = None,
    kb_chunk_count: KbChunkCount = None,
    kb_similarity_threshold: KbSimilarityThreshold = None,
    eot_threshold: EotThreshold = None,
    eager_eot_threshold: EagerEotThreshold = None,
    eot_timeout_ms: EotTimeoutMs = None,
) -> dict[str, Any]:
    """Create a voice agent (POST /agents).

    Required: name, system_prompt. Every other field is optional; an omitted field
    takes the server default named in its description. The voice is bound to the
    agent record, so every call this agent handles uses it.

    Returns the created agent: id (UUID, use it in every other agent/call tool) plus
    all fields above except webhook_secret. Errors with HTTP 422 and the validation
    detail when a value is out of bounds (e.g. eager_eot_threshold > eot_threshold).
    """
    return await _client.request("POST", "/agents", json=_body(
        name=name, system_prompt=system_prompt, language=language, voice=voice,
        phone_number=phone_number, disclosure_message=disclosure_message, tools=tools,
        webhook_url=webhook_url, webhook_secret=webhook_secret, variables=variables,
        max_call_duration=max_call_duration, is_active=is_active,
        knowledge_base_id=knowledge_base_id, kb_chunk_count=kb_chunk_count,
        kb_similarity_threshold=kb_similarity_threshold, eot_threshold=eot_threshold,
        eager_eot_threshold=eager_eot_threshold, eot_timeout_ms=eot_timeout_ms,
    ))


async def agent_update(
    agent_id: AgentId,
    name: Name = None,
    system_prompt: SystemPrompt = None,
    language: Language = None,
    voice: Voice = None,
    phone_number: PhoneNumber = None,
    disclosure_message: DisclosureMessage = None,
    tools: Tools = None,
    webhook_url: WebhookUrl = None,
    webhook_secret: WebhookSecret = None,
    variables: Variables = None,
    max_call_duration: MaxCallDuration = None,
    is_active: IsActive = None,
    knowledge_base_id: KnowledgeBaseId = None,
    kb_chunk_count: KbChunkCount = None,
    kb_similarity_threshold: KbSimilarityThreshold = None,
    eot_threshold: EotThreshold = None,
    eager_eot_threshold: EagerEotThreshold = None,
    eot_timeout_ms: EotTimeoutMs = None,
    clear_fields: Annotated[
        list[ClearableField] | None,
        Field(description="Fields to reset to null (e.g. [\"eot_threshold\"] to return to the Flux "
              "default, [\"knowledge_base_id\"] to detach the knowledge base). Must not also be set "
              "in the same call."),
    ] = None,
) -> dict[str, Any]:
    """Change some fields of an existing agent (PATCH /agents/{agent_id}).

    Required: agent_id plus at least one field to set or clear. Only the fields you
    pass are sent; an omitted (or null) field is left unchanged. To set a nullable
    field back to null, name it in clear_fields. Changes apply to the next call,
    not to a call already in progress.

    Returns the full updated agent (same shape as agent_get). Errors: HTTP 404 if
    the agent does not exist; HTTP 422 if a value is out of bounds or the merged
    eager_eot_threshold would exceed the effective eot_threshold.
    """
    body = _body(
        name=name, system_prompt=system_prompt, language=language, voice=voice,
        phone_number=phone_number, disclosure_message=disclosure_message, tools=tools,
        webhook_url=webhook_url, webhook_secret=webhook_secret, variables=variables,
        max_call_duration=max_call_duration, is_active=is_active,
        knowledge_base_id=knowledge_base_id, kb_chunk_count=kb_chunk_count,
        kb_similarity_threshold=kb_similarity_threshold, eot_threshold=eot_threshold,
        eager_eot_threshold=eager_eot_threshold, eot_timeout_ms=eot_timeout_ms,
    )
    for field in clear_fields or []:
        if field in body:
            raise ToolError(f"{field} is both set and listed in clear_fields; pick one")
        body[field] = None
    if not body:
        raise ToolError("agent_update needs at least one field to set or clear")
    return await _client.request("PATCH", f"/agents/{agent_id}", json=body)


async def agent_list(
    active_only: Annotated[
        bool, Field(description="true (default) = only active agents; false = include deactivated ones.")
    ] = True,
) -> list[dict[str, Any]]:
    """List agents, newest first (GET /agents).

    Returns a list of agent records (same shape as agent_get); empty list if none.
    """
    return await _client.request("GET", "/agents", params={"active_only": active_only})


async def agent_get(agent_id: AgentId) -> dict[str, Any]:
    """Fetch one agent by id (GET /agents/{agent_id}), active or not.

    Returns the agent record: id, name, system_prompt, language, voice, phone_number,
    disclosure_message, tools, webhook_url, variables, max_call_duration, is_active,
    knowledge_base_id, kb_chunk_count, kb_similarity_threshold, eot_threshold,
    eager_eot_threshold, eot_timeout_ms (null = Flux default). webhook_secret is
    never returned. Errors with HTTP 404 if the id does not exist.
    """
    return await _client.request("GET", f"/agents/{agent_id}")


async def agent_delete(agent_id: AgentId) -> dict[str, Any]:
    """Deactivate an agent (DELETE /agents/{agent_id}). It stops taking calls immediately.

    This is a soft delete: the record and its call history are kept and is_active
    becomes false. The agent then takes no inbound calls, call_start refuses it,
    and agent_list hides it unless active_only=false. To undo, call agent_update
    with is_active=true. Repeating the call on an inactive agent is a no-op.

    Returns {"agent_id": ..., "is_active": false}. Errors with HTTP 404 if the id
    does not exist.
    """
    await _client.request("DELETE", f"/agents/{agent_id}")
    return {"agent_id": str(agent_id), "is_active": False}


async def call_start(
    agent_id: AgentId,
    to_number: Annotated[str, Field(description="Number to dial in E.164 format, e.g. +5215512345678.")],
) -> dict[str, Any]:
    """Place a REAL outbound phone call through Telnyx (POST /calls/outbound).

    This dials a real phone and costs money. The agent must be active and have a
    phone_number (used as caller ID). Returns as soon as Telnyx accepts the dial:
    {"call_id", "status": "initiated", "to_number", "from_number"}. Use call_status
    with call_id to follow the call.

    Errors: HTTP 404 agent missing or inactive; 400 agent has no phone_number;
    500 server lacks TELNYX_CONNECTION_ID or APP_PUBLIC_URL; 502 Telnyx refused or
    could not be reached. On a 502 the call was NOT placed, but the API has already
    stored an "in_progress" call row for it that stays orphaned (no call_id is
    returned; visible only via the REST API, not through any MCP tool). Do not treat that row as a live call.
    """
    return await _client.request(
        "POST", "/calls/outbound", json={"agent_id": str(agent_id), "to_number": to_number}
    )


async def call_status(call_id: CallId) -> dict[str, Any]:
    """Get a call's state, transcript and per-turn latency (GET /calls/{call_id} + /latency).

    Returns the call record: id, agent_id, from_number, to_number, started_at,
    ended_at, duration_seconds, recording_url, status, transcripts (list of
    {role, content, timestamp_ms}), plus "latency": {"call_id", "turns": [{turn_index,
    stt_ms, llm_ttft_ms, tts_ttfb_ms, e2e_ms, ttfa_ms, prerecorded_hits}], "summary":
    {avg_stt_ms, avg_llm_ttft_ms, avg_tts_ttfb_ms, avg_e2e_ms, avg_ttfa_ms, turn_count}}
    (all times in ms), or "latency": null if the latency endpoint returns 404.

    status is "in_progress" from the moment the call is created until the media
    stream closes, then "completed". "completed" means the stream closed for ANY
    reason (normal hang-up, max duration, pipeline error, missing stream_id or
    agent), not that a conversation happened: check that transcripts is non-empty
    and latency.summary.turn_count > 0. An outbound call that is never answered
    stays "in_progress" with ended_at null; the server does not record that hangup.
    Errors with HTTP 404 if the call id does not exist.
    """
    call = await _client.request("GET", f"/calls/{call_id}")
    try:
        call["latency"] = await _client.request("GET", f"/calls/{call_id}/latency")
    except PipesongAPIError as e:
        if e.status != 404:
            raise
        call["latency"] = None
    return call
