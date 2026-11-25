"""The agent's connection to the MCP gateway, its only route to tools.

``GatewaySession`` bundles, for one agent session (one LangGraph thread):

* the MCP client over Streamable HTTP (tool discovery and tool calls), and
* the gateway's control-plane endpoints the runtime needs: its own approvals (to resume
  after a human decision) and model-usage reports (so the gateway's budget covers LLM
  spend as well as tool calls).

Both authenticate with the same bearer token, whose ``sid`` claim is the session.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Final, Self

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.dispatcher import ProgressFnT
from mcp_types import CallToolResult, TextContent, Tool
from opentelemetry import propagate

GOVERNANCE_META_KEY: Final = "mcp-enterprise-gateway/governance"

ProgressCallback = ProgressFnT


class GatewayError(Exception):
    """The gateway's control plane refused or failed a request."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class Governance:
    """The gateway's verdict attached to a tool result (``_meta`` governance block)."""

    code: str
    details: dict[str, Any]

    @classmethod
    def of(cls, result: CallToolResult) -> Governance | None:
        block = (result.meta or {}).get(GOVERNANCE_META_KEY)
        if not isinstance(block, dict) or not isinstance(block.get("code"), str):
            return None
        return cls(code=block["code"], details=dict(block))


def result_text(result: CallToolResult) -> str:
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


class GatewaySession:
    """Authenticated MCP + control-plane access for one agent session."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        session_id: str,
        timeout_seconds: float = 60.0,
        transport: httpx2.AsyncBaseTransport | None = None,
        mcp: bool = True,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._with_mcp = mcp
        self.session_id = session_id
        self._token = token
        self._timeout = timeout_seconds
        self._transport = transport
        self._stack: AsyncExitStack | None = None
        self._http: httpx2.AsyncClient | None = None
        self._mcp: Client | None = None

    async def __aenter__(self) -> Self:
        stack = AsyncExitStack()
        self._http = await stack.enter_async_context(
            httpx2.AsyncClient(
                base_url=self.base_url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=httpx2.Timeout(30.0, read=self._timeout),
                transport=self._transport,
            )
        )
        self._stack = stack
        if self._with_mcp:
            # Entered here, in the caller's task: the MCP client runs an anyio task group,
            # which must be exited by the task that entered it (not by a tool-call task).
            try:
                self._mcp = await stack.enter_async_context(
                    Client(
                        streamable_http_client(f"{self.base_url}/mcp", http_client=self._http),
                        read_timeout_seconds=self._timeout,
                        cache=None,
                    )
                )
            except BaseException:
                await stack.aclose()
                self._stack = self._http = None
                raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        stack, self._stack, self._http, self._mcp = self._stack, None, None, None
        if stack is not None:
            await stack.__aexit__(exc_type, exc, traceback)

    # --- MCP ------------------------------------------------------------------------------
    async def list_tools(self) -> list[Tool]:
        """Every tool the gateway exposes to this identity (already filtered by policy)."""
        client = await self._client()
        tools: list[Tool] = []
        cursor: str | None = None
        while True:
            page = await client.list_tools(cursor=cursor)
            tools.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                return tools

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        on_progress: ProgressCallback | None = None,
    ) -> CallToolResult:
        client = await self._client()
        return await client.call_tool(name, arguments, progress_callback=on_progress)

    # --- control plane --------------------------------------------------------------------
    async def approval(self, approval_id: str) -> dict[str, Any]:
        """One of this session's own approvals, with its audit trail and (once executed)
        the upstream result."""
        return await self._request("GET", f"/v1/approvals/{approval_id}")

    async def report_usage(self, report: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", f"/v1/sessions/{self.session_id}/usage", json=report)

    async def session_status(self) -> dict[str, Any] | None:
        try:
            return await self._request("GET", f"/v1/sessions/{self.session_id}")
        except GatewayError as exc:
            if exc.status_code == httpx2.codes.NOT_FOUND:
                return None  # nothing charged to this session yet
            raise

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self._http is None:
            msg = "GatewaySession is not open"
            raise RuntimeError(msg)
        headers: dict[str, str] = {}
        propagate.inject(headers)  # continue the agent's trace in the gateway
        try:
            response = await self._http.request(method, path, headers=headers, **kwargs)
        except httpx2.HTTPError as exc:
            msg = f"gateway unreachable ({type(exc).__name__})"
            raise GatewayError(msg) from exc
        if response.is_error:
            detail = _problem_detail(response)
            msg = f"gateway answered {response.status_code} to {method} {path}: {detail}"
            raise GatewayError(msg, status_code=response.status_code)
        body: dict[str, Any] = response.json()
        return body

    async def _client(self) -> Client:
        if self._mcp is None:
            msg = "GatewaySession is not open with MCP (mcp=True)"
            raise RuntimeError(msg)
        return self._mcp


def _problem_detail(response: httpx2.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict):
        return str(body.get("detail") or body.get("title") or body)[:200]
    return str(body)[:200]
