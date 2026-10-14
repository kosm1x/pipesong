"""B.1 MCP tools: each tool's exact REST request, its parsed return, the error path,
the routes it targets existing on the real app, and the server's tool listing.

The REST layer is an httpx.MockTransport (the API needs Postgres: the KB model has a
pgvector column, so SQLite :memory: cannot host it). Canned responses are built from
the API's own Pydantic response models, so a response-shape change breaks these tests.
"""
import json
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from starlette.routing import Match

from pipesong.api.agents import AgentCreate, AgentResponse, AgentUpdate
from pipesong.api.calls import CallDetailResponse, CallLatencyResponse, TranscriptResponse, TurnLatencyResponse
from pipesong.api.outbound import OutboundCallCreate, OutboundCallResponse
from pipesong.mcp import tools
from pipesong.mcp.client import PipesongAPIError, PipesongClient
from pipesong.mcp.server import mcp
from pipesong.models.agent import Agent

AGENT_ID = uuid.uuid4()
CALL_ID = uuid.uuid4()
KB_ID = uuid.uuid4()

AGENT = AgentResponse(
    id=AGENT_ID, name="Recepción", system_prompt="Eres una recepcionista.", language="es", voice="em_alex",
    phone_number="+5215500000000", disclosure_message="Esta llamada está siendo grabada.", tools=None,
    webhook_url=None, variables={"clinica": "Centro"}, max_call_duration=600, is_active=True,
    knowledge_base_id=None, kb_chunk_count=3, kb_similarity_threshold=0.5,
    eot_threshold=0.8, eager_eot_threshold=None, eot_timeout_ms=3000,
).model_dump(mode="json")

CALL = CallDetailResponse(
    id=CALL_ID, agent_id=AGENT_ID, from_number="+5215500000000", to_number="+5215511111111",
    started_at=datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc), ended_at=None, duration_seconds=None,
    recording_url=None, status="in_progress",
    transcripts=[TranscriptResponse(role="assistant", content="Hola", timestamp_ms=120)],
).model_dump(mode="json")

LATENCY = CallLatencyResponse(
    call_id=CALL_ID,
    turns=[TurnLatencyResponse(turn_index=0, stt_ms=80.0, llm_ttft_ms=210.0, tts_ttfb_ms=95.0, e2e_ms=420.0,
                               ttfa_ms=450.0, prerecorded_hits=1)],
    summary={"avg_stt_ms": 80.0, "avg_llm_ttft_ms": 210.0, "avg_tts_ttfb_ms": 95.0, "avg_e2e_ms": 420.0,
             "avg_ttfa_ms": 450.0, "turn_count": 1},
).model_dump(mode="json")

OUTBOUND = OutboundCallResponse(
    call_id=CALL_ID, status="initiated", to_number="+5215511111111", from_number="+5215500000000",
).model_dump(mode="json")


class FakeAPI:
    """MockTransport handler: canned (status, json) per (method, path); records every request."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        status, payload = self.routes.get((request.method, request.url.path), (404, {"detail": "Not Found"}))
        return httpx.Response(status, json=payload) if payload is not None else httpx.Response(status)

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]

    def body(self, i: int = -1):
        content = self.requests[i].content
        return json.loads(content) if content else None


DEFAULT_ROUTES = {
    ("GET", "/health"): (200, {"status": "ok"}),
    ("POST", "/agents"): (201, AGENT),
    ("GET", "/agents"): (200, [AGENT]),
    ("GET", f"/agents/{AGENT_ID}"): (200, AGENT),
    ("PATCH", f"/agents/{AGENT_ID}"): (200, AGENT),
    ("DELETE", f"/agents/{AGENT_ID}"): (204, None),
    ("POST", "/calls/outbound"): (201, OUTBOUND),
    ("GET", f"/calls/{CALL_ID}"): (200, CALL),
    ("GET", f"/calls/{CALL_ID}/latency"): (200, LATENCY),
}


@pytest.fixture
def api(monkeypatch):
    fake = FakeAPI(dict(DEFAULT_ROUTES))
    monkeypatch.setattr(
        tools, "_client",
        PipesongClient(base_url="http://pipesong.test", api_key="", transport=httpx.MockTransport(fake)),
    )
    return fake


# --- One test per tool: exact request out, parsed response back ---


async def test_health(api):
    assert await tools.health() == {"status": "ok"}
    assert (api.last.method, api.last.url.path) == ("GET", "/health")


async def test_agent_create_sends_only_set_fields(api):
    result = await tools.agent_create(name="Recepción", system_prompt="Eres una recepcionista.")
    assert (api.last.method, api.last.url.path) == ("POST", "/agents")
    assert api.body() == {"name": "Recepción", "system_prompt": "Eres una recepcionista."}
    assert result == AGENT


async def test_agent_create_full_payload(api):
    await tools.agent_create(
        name="A", system_prompt="P", voice="ef_dora", knowledge_base_id=KB_ID, eot_threshold=0.8,
        eager_eot_threshold=0.6, eot_timeout_ms=3000, variables={"k": "v"}, is_active=False,
    )
    assert api.body() == {
        "name": "A", "system_prompt": "P", "voice": "ef_dora", "knowledge_base_id": str(KB_ID),
        "eot_threshold": 0.8, "eager_eot_threshold": 0.6, "eot_timeout_ms": 3000, "variables": {"k": "v"},
        "is_active": False,
    }


async def test_agent_update_patches_only_given_fields(api):
    result = await tools.agent_update(AGENT_ID, voice="ef_dora", eot_threshold=0.8, eot_timeout_ms=3000)
    assert (api.last.method, api.last.url.path) == ("PATCH", f"/agents/{AGENT_ID}")
    assert api.body() == {"voice": "ef_dora", "eot_threshold": 0.8, "eot_timeout_ms": 3000}
    assert result == AGENT


async def test_agent_update_clear_fields_sends_explicit_null(api):
    await tools.agent_update(AGENT_ID, eot_timeout_ms=4000, clear_fields=["eot_threshold", "knowledge_base_id"])
    assert api.body() == {"eot_timeout_ms": 4000, "eot_threshold": None, "knowledge_base_id": None}


async def test_agent_update_rejects_set_and_clear_of_same_field(api):
    with pytest.raises(ToolError, match="eot_threshold"):
        await tools.agent_update(AGENT_ID, eot_threshold=0.8, clear_fields=["eot_threshold"])
    with pytest.raises(ToolError, match="at least one field"):
        await tools.agent_update(AGENT_ID)
    assert api.requests == []


async def test_agent_list(api):
    assert await tools.agent_list() == [AGENT]
    assert (api.last.method, api.last.url.path, api.last.url.params["active_only"]) == ("GET", "/agents", "true")
    await tools.agent_list(active_only=False)
    assert api.last.url.params["active_only"] == "false"


async def test_agent_get(api):
    assert await tools.agent_get(AGENT_ID) == AGENT
    assert (api.last.method, api.last.url.path) == ("GET", f"/agents/{AGENT_ID}")


async def test_agent_delete(api):
    assert await tools.agent_delete(AGENT_ID) == {"agent_id": str(AGENT_ID), "is_active": False}
    assert (api.last.method, api.last.url.path) == ("DELETE", f"/agents/{AGENT_ID}")
    assert api.last.content == b""


async def test_call_start(api):
    assert await tools.call_start(AGENT_ID, "+5215511111111") == OUTBOUND
    assert (api.last.method, api.last.url.path) == ("POST", "/calls/outbound")
    assert api.body() == {"agent_id": str(AGENT_ID), "to_number": "+5215511111111"}


async def test_call_status_merges_latency(api):
    result = await tools.call_status(CALL_ID)
    assert [(r.method, r.url.path) for r in api.requests] == [
        ("GET", f"/calls/{CALL_ID}"), ("GET", f"/calls/{CALL_ID}/latency"),
    ]
    assert result == {**CALL, "latency": LATENCY}


# --- Error paths ---


async def test_call_status_latency_404_is_null(api):
    del api.routes[("GET", f"/calls/{CALL_ID}/latency")]
    assert await tools.call_status(CALL_ID) == {**CALL, "latency": None}


async def test_call_status_latency_other_error_raises(api):
    api.routes[("GET", f"/calls/{CALL_ID}/latency")] = (500, {"detail": "boom"})
    with pytest.raises(PipesongAPIError) as exc:
        await tools.call_status(CALL_ID)
    assert exc.value.status == 500


async def test_non_2xx_raises_with_status_and_body(api):
    api.routes[("POST", "/calls/outbound")] = (400, {"detail": "Agent has no phone number configured"})
    with pytest.raises(PipesongAPIError) as exc:
        await tools.call_start(AGENT_ID, "+5215511111111")
    assert exc.value.status == 400
    assert "HTTP 400" in str(exc.value) and "Agent has no phone number configured" in str(exc.value)
    assert "POST /calls/outbound" in str(exc.value)


async def test_unreachable_api_raises_clear_error(monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("Connection refused", request=request)

    monkeypatch.setattr(tools, "_client", PipesongClient(
        base_url="http://pipesong.test", api_key="", transport=httpx.MockTransport(refuse)))
    with pytest.raises(PipesongAPIError) as exc:
        await tools.health()
    assert exc.value.status is None
    assert "unreachable" in str(exc.value) and "http://pipesong.test" in str(exc.value)


async def test_bearer_token_sent_only_when_api_key_set():
    fake = FakeAPI(dict(DEFAULT_ROUTES))
    await PipesongClient(base_url="http://t", api_key="s3cret", transport=httpx.MockTransport(fake)).request(
        "GET", "/health")
    await PipesongClient(base_url="http://t", api_key="", transport=httpx.MockTransport(fake)).request(
        "GET", "/health")
    assert fake.requests[0].headers["authorization"] == "Bearer s3cret"
    assert "authorization" not in fake.requests[1].headers


# --- Coupling to the real REST surface ---


async def test_every_request_matches_a_real_route(api):
    """Run every tool, then match each request (method + path) against the real FastAPI app."""
    from pipesong.main import app

    await tools.health()
    await tools.agent_create(name="A", system_prompt="P")
    await tools.agent_update(AGENT_ID, voice="ef_dora")
    await tools.agent_list()
    await tools.agent_get(AGENT_ID)
    await tools.agent_delete(AGENT_ID)
    await tools.call_start(AGENT_ID, "+5215511111111")
    await tools.call_status(CALL_ID)
    assert len(api.requests) == 9

    def routed(method: str, path: str) -> bool:
        # Starlette's method-aware match: a wrong method on a real path is only PARTIAL.
        scope = {"type": "http", "path": path, "method": method}
        return any(r.matches(scope)[0] == Match.FULL for r in app.routes)

    for req in api.requests:
        assert routed(req.method, req.url.path), f"no route on the real app for {req.method} {req.url.path}"
    # The check can fail: a wrong method or a renamed path does not match.
    assert not routed("PUT", f"/agents/{AGENT_ID}")
    assert not routed("POST", "/calls/start")


def _params(tool_name: str) -> set[str]:
    return set(mcp._tool_manager.get_tool(tool_name).parameters["properties"])


def _bounds(schema: dict) -> tuple:
    for s in [schema, *schema.get("anyOf", [])]:
        if "minimum" in s or "maximum" in s:
            return s.get("minimum"), s.get("maximum")
    return None, None


def test_tool_params_mirror_the_rest_models():
    assert _params("agent_create") == set(AgentCreate.model_fields)
    assert _params("agent_update") - {"agent_id", "clear_fields"} == set(AgentUpdate.model_fields)
    # OutboundCallCreate.variables is accepted by the API but never applied (api/outbound.py
    # drops it), so call_start does not expose it.
    assert _params("call_start") == set(OutboundCallCreate.model_fields) - {"variables"}

    for tool_name, model in (("agent_create", AgentCreate), ("agent_update", AgentUpdate)):
        rest = model.model_json_schema()["properties"]
        props = mcp._tool_manager.get_tool(tool_name).parameters["properties"]
        for field in model.model_fields:
            assert _bounds(props[field]) == _bounds(rest[field]), (tool_name, field)


def test_clear_fields_are_exactly_the_nullable_agent_columns():
    nullable = {c.key for c in Agent.__table__.columns if c.nullable and not c.primary_key}
    nullable.discard("created_at")
    schema = mcp._tool_manager.get_tool("agent_update").parameters["properties"]["clear_fields"]
    enum = set(schema["anyOf"][0]["items"]["enum"])
    assert enum == nullable


# --- Server smoke over the SDK's in-memory client ---

EXPECTED_REQUIRED = {
    "health": set(),
    "agent_create": {"name", "system_prompt"},
    "agent_update": {"agent_id"},
    "agent_list": set(),
    "agent_get": {"agent_id"},
    "agent_delete": {"agent_id"},
    "call_start": {"agent_id", "to_number"},
    "call_status": {"call_id"},
}


async def test_server_lists_exactly_the_eight_tools():
    async with Client(mcp) as client:
        listed = (await client.list_tools()).tools
    assert {t.name for t in listed} == set(EXPECTED_REQUIRED)
    for t in listed:
        assert set(t.input_schema.get("required", [])) == EXPECTED_REQUIRED[t.name], t.name
        assert t.description, t.name
    by_name = {t.name: t for t in listed}
    assert by_name["agent_delete"].annotations.destructive_hint is True
    assert by_name["call_start"].annotations.open_world_hint is True


async def test_server_call_roundtrip_and_error_text_reaches_caller(api):
    async with Client(mcp) as client:
        ok = await client.call_tool("agent_get", {"agent_id": str(AGENT_ID)})
        assert not ok.is_error
        assert ok.structured_content == AGENT

        missing = uuid.uuid4()
        err = await client.call_tool("agent_get", {"agent_id": str(missing)})
        assert err.is_error
        assert "HTTP 404" in err.content[0].text and "Not Found" in err.content[0].text

        n = len(api.requests)
        bad = await client.call_tool("agent_update", {"agent_id": str(AGENT_ID), "eot_threshold": 0.95})
        assert bad.is_error and "eot_threshold" in bad.content[0].text
        assert len(api.requests) == n  # rejected by the input schema, never reached the API


# --- Configuration: namespaced process env only, never a cwd .env ---

REPO = Path(__file__).resolve().parents[1]


def _run_in(cwd: Path, code: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, cwd=cwd,
                          env={"PYTHONPATH": str(REPO / "src"), "PATH": "/usr/bin:/bin", **env})


def test_foreign_dotenv_and_generic_api_key_are_ignored(tmp_path):
    # A client launched in another project: its .env holds a generic API_KEY plus a key
    # the app Settings would reject as extra; the parent env also carries API_KEY.
    (tmp_path / ".env").write_text("API_KEY=x\nOPENAI_API_KEY=y\n")
    code = ("import pipesong.mcp.server; from pipesong.mcp.client import PipesongClient; "
            "c = PipesongClient(); print(repr(c.api_key)); print(c.base_url)")
    out = _run_in(tmp_path, code, PIPESONG_API_URL="http://pipesong.test:9000/", API_KEY="parent-key")
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["''", "http://pipesong.test:9000"]


def test_server_refuses_to_start_without_api_url(tmp_path):
    out = subprocess.run([sys.executable, "-m", "pipesong.mcp.server"], capture_output=True, text=True,
                         timeout=60, cwd=tmp_path, stdin=subprocess.DEVNULL,
                         env={"PYTHONPATH": str(REPO / "src"), "PATH": "/usr/bin:/bin"})
    assert out.returncode != 0
    assert "PIPESONG_API_URL is not set" in out.stderr


async def test_missing_api_url_is_a_clear_tool_error(monkeypatch):
    monkeypatch.delenv("PIPESONG_API_URL", raising=False)
    monkeypatch.setattr(tools, "_client", PipesongClient())
    with pytest.raises(ToolError, match="PIPESONG_API_URL is not set"):
        await tools.health()


async def test_env_api_key_is_sent_as_bearer(monkeypatch):
    fake = FakeAPI(dict(DEFAULT_ROUTES))
    monkeypatch.setenv("PIPESONG_API_URL", "http://pipesong.test")
    monkeypatch.setenv("PIPESONG_API_KEY", "op-key")
    await PipesongClient(transport=httpx.MockTransport(fake)).request("GET", "/health")
    assert fake.last.headers["authorization"] == "Bearer op-key"
    assert str(fake.last.url) == "http://pipesong.test/health"


async def test_long_error_body_is_truncated(api):
    api.routes[("GET", f"/agents/{AGENT_ID}")] = (500, {"detail": "x" * 10000})
    with pytest.raises(PipesongAPIError) as exc:
        await tools.agent_get(AGENT_ID)
    assert len(exc.value.body) < 2200 and "truncated" in exc.value.body


async def test_non_json_2xx_body_is_an_api_error(monkeypatch):
    html = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>not the API</html>"))
    monkeypatch.setattr(tools, "_client", PipesongClient(base_url="http://t", api_key="", transport=html))
    with pytest.raises(PipesongAPIError) as exc:
        await tools.health()
    assert exc.value.status == 200 and "non-JSON" in str(exc.value) and "<html>" in str(exc.value)


def test_importing_the_server_does_not_import_pipecat():
    code = "import sys, pipesong.mcp.server; print('pipecat' in sys.modules, 'pipesong.pipeline' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                         env={"PYTHONPATH": "src", "PATH": "/usr/bin:/bin"}, check=True,
                         cwd=Path(__file__).resolve().parents[1])
    assert out.stdout.split() == ["False", "False"]
