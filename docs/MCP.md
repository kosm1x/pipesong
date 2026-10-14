# Pipesong MCP server

Lets an agent (or a developer in an agentic IDE) create and tune Pipesong voice agents
and place test calls without hand-writing REST requests. It is a thin stdio MCP server
over the existing REST API (roadmap B.1): every tool is one or two HTTP calls to a running
Pipesong API. It needs no GPU, Kokoro, Deepgram or Telnyx credentials of its own, and it
does not import pipecat.

## Run

The API must be running. From the repo root:

```bash
PIPESONG_API_URL=http://127.0.0.1:8080 PIPESONG_API_KEY=<the API's API_KEY> \
  PYTHONPATH=src .venv/bin/python -m pipesong.mcp.server
```

- `PIPESONG_API_URL` (required): base URL of the API. Unset = the server refuses to start
  and names the variable. There is no default: on this VPS `:8080` is mission-control.
- `PIPESONG_API_KEY` (optional): the operator sets it to the API's `API_KEY` value when
  the API enforces one; it is sent as `Authorization: Bearer <key>`. `health` is
  auth-exempt, so a 401 on any other tool means this key is wrong or unset.
- Only these two process env vars are read. No `.env` file is loaded on the MCP path, so
  a client launched in another project never picks up that project's secrets.

## Client config (Claude Code, other stdio MCP clients)

`.mcp.json` in the project, or `claude mcp add`:

```json
{
  "mcpServers": {
    "pipesong": {
      "command": "/root/claude/pipesong/.venv/bin/python",
      "args": ["-m", "pipesong.mcp.server"],
      "env": {
        "PYTHONPATH": "/root/claude/pipesong/src",
        "PIPESONG_API_URL": "http://127.0.0.1:8080",
        "PIPESONG_API_KEY": "<the API's API_KEY, or omit if the API has none>"
      }
    }
  }
}
```

Set `PIPESONG_API_URL` to wherever the Pipesong API actually listens.

## Tools

| Tool | REST | What it does |
| --- | --- | --- |
| `health` | `GET /health` | Liveness probe; call it first. |
| `agent_create` | `POST /agents` | Create an agent; `name` + `system_prompt` required, the rest take server defaults. |
| `agent_update` | `PATCH /agents/{id}` | Change only the fields passed (voice, prompt, `eot_*`, KB, ...); `clear_fields` resets nullable fields to null. |
| `agent_list` | `GET /agents` | Agents, newest first; `active_only=false` includes deactivated ones. |
| `agent_get` | `GET /agents/{id}` | One agent record (`webhook_secret` is never returned). |
| `agent_delete` | `DELETE /agents/{id}` | Soft delete: sets `is_active=false`; undo with `agent_update(is_active=true)`. |
| `call_start` | `POST /calls/outbound` | Dials a REAL number via Telnyx; the agent needs a `phone_number` (caller ID). |
| `call_status` | `GET /calls/{id}` + `/latency` | Call record + transcript, with per-turn latency under `latency` (`null` if absent). |

Voice binding lives on the agent record (`voice`), so the agent is the parameter, not a
header. A non-2xx response comes back as a tool error carrying the HTTP status and the
API's response body (e.g. a 422 validation detail).

## Tests

`tests/test_mcp_tools.py` mocks the REST layer with `httpx.MockTransport` (canned bodies
built from the API's own response models), checks every request against the real app's
routes, checks tool params and bounds against the API's request models, and lists/calls
the tools through the SDK's in-memory client. SQLite `:memory:` cannot host the API's
schema (the knowledge-base model has a pgvector column), hence the mock.

## Gate B.2 (GPU window)

Through this MCP, an agent creates a PipeSong agent, places a test call with `call_start`,
and reads the finished call back with `call_status` (transcript + latency), end to end.
