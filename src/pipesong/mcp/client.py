"""Thin async client over the Pipesong REST API, used by the MCP tools.

Configuration comes ONLY from two namespaced process env vars, read at request time:
PIPESONG_API_URL (required) and PIPESONG_API_KEY (optional; the API's API_KEY value).
It deliberately does not use the app Settings (config.py): that BaseSettings loads `.env` from the
process cwd, so an MCP client launched in another project would hand that project's
API_KEY to whatever answers at the URL, and any unknown `.env` key aborts the import.
"""
import logging
import os
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

logger = logging.getLogger(__name__)

API_URL_ENV = "PIPESONG_API_URL"
API_KEY_ENV = "PIPESONG_API_KEY"
_BODY_LIMIT = 2048


def api_url_from_env() -> str:
    """Return PIPESONG_API_URL without a trailing slash; ToolError if it is unset or empty."""
    url = os.environ.get(API_URL_ENV, "").strip()
    if not url:
        raise ToolError(
            f"{API_URL_ENV} is not set: export it to the Pipesong REST API base URL "
            f"(e.g. http://127.0.0.1:8080) in the MCP server's environment"
        )
    return url.rstrip("/")


class PipesongAPIError(ToolError):
    """A REST call failed: a non-2xx response, a non-JSON body, or the API could not be reached.

    Subclasses the SDK's `ToolError` so the message (status + body) reaches the MCP
    caller; any other exception type is reduced to "Error executing tool <name>".
    """

    def __init__(self, method: str, path: str, status: int | None, body: str):
        if len(body) > _BODY_LIMIT:
            body = body[:_BODY_LIMIT] + f"... [truncated, {len(body)} chars]"
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        if status is None:
            msg = f"Pipesong API unreachable ({method} {path}): {body}"
        else:
            msg = f"Pipesong API {method} {path} returned HTTP {status}: {body}"
        super().__init__(msg)


class PipesongClient:
    """One short-lived httpx client per request. Explicit args (tests) win over the env."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ):
        self._base_url = base_url
        self._api_key = api_key
        self.transport = transport
        self.timeout = timeout

    @property
    def base_url(self) -> str:
        return self._base_url.rstrip("/") if self._base_url else api_url_from_env()

    @property
    def api_key(self) -> str:
        return self._api_key if self._api_key is not None else os.environ.get(API_KEY_ENV, "").strip()

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Send one request; return the parsed JSON body, or None for an empty body (204)."""
        base_url = self.base_url
        api_key = self.api_key
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        try:
            async with httpx.AsyncClient(
                base_url=base_url, transport=self.transport, timeout=self.timeout, headers=headers
            ) as client:
                resp = await client.request(method, path, json=json, params=params)
        except httpx.HTTPError as e:
            logger.warning("Pipesong API %s %s failed: %r", method, path, e)
            raise PipesongAPIError(method, path, None, f"{type(e).__name__}: {e} (base URL {base_url})") from e
        if not resp.is_success:
            raise PipesongAPIError(method, path, resp.status_code, resp.text)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as e:
            raise PipesongAPIError(method, path, resp.status_code, f"non-JSON response body: {resp.text}") from e
