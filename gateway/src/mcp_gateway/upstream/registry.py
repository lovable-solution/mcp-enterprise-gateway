"""Catalog of upstream tools and the client used to call them.

Discovery lists every upstream's tools and keeps only those registered in the policy
(default deny: an unregistered upstream tool is invisible and uncallable). Each kept tool
is re-published under its qualified name with annotations derived from the policy, never
from the upstream's own hints, and, with ``strict_tool_arguments``, with a closed input
schema. The same schema validates arguments before a call is forwarded or held for
approval, so an operator never reviews (and no upstream ever receives) a payload outside
the declared contract.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Final, Literal

import httpx2
import jsonschema
from jsonschema.protocols import Validator
from mcp import Client, MCPError
from mcp.client.streamable_http import streamable_http_client
from mcp_types import INTERNAL_ERROR, CallToolResult, Tool, ToolAnnotations

from ..policy.models import AccessLevel, PolicyDocument, ToolPolicy
from ..telemetry import Attr, Instruments
from .identity import GATEWAY_IDENTITY, AssertionSigner, CallerIdentity

logger = logging.getLogger(__name__)

_DISCOVERY_TIMEOUT_SECONDS: Final = 10.0
_RETRY_SECONDS: Final = 5.0

APPROVAL_NOTE = (
    "Calls are held for human approval before they run; a pending call returns an approval "
    "id instead of a result."
)


Executed = Literal["no", "unknown"]


class UpstreamError(Exception):
    """An upstream server could not be reached or refused the request.

    ``executed`` is ``"no"`` only when the upstream provably did not run the call (the
    connection was never established, or it answered with a protocol error), so a budget
    charge may be refunded. Anything else, such as a timeout after the request was sent,
    is ``"unknown"``: the call may have taken effect.
    """

    def __init__(self, message: str, *, executed: Executed) -> None:
        super().__init__(message)
        self.executed: Executed = executed


def _never_reached(exc: BaseException) -> bool:
    """True when the exception chain shows the request never reached the upstream."""
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, httpx2.ConnectError | httpx2.ConnectTimeout):
            return True
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        pending.extend(e for e in (current.__cause__, current.__context__) if e is not None)
    return False


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    name: str
    server: str
    upstream_tool: str
    tool: Tool
    validator: Validator = field(repr=False)

    def argument_errors(self, arguments: Mapping[str, Any]) -> list[str]:
        errors = sorted(self.validator.iter_errors(dict(arguments)), key=lambda e: list(e.path))
        return [
            f"{'.'.join(str(p) for p in error.path) or '(arguments)'}: {error.message}"
            for error in errors
        ]


@dataclass(slots=True)
class _ServerState:
    entries: dict[str, CatalogEntry] = field(default_factory=dict)
    fetched_at: float = float("-inf")
    error: str | None = None
    ever_fetched: bool = False
    refresh: asyncio.Task[None] | None = None


class UpstreamRegistry:
    def __init__(
        self,
        *,
        upstreams: Mapping[str, str],
        policy: PolicyDocument,
        signer: AssertionSigner,
        instruments: Instruments,
        timeout_seconds: float,
        catalog_ttl_seconds: float,
        strict_arguments: bool,
    ) -> None:
        self._upstreams = dict(upstreams)
        self._policy = policy
        self._signer = signer
        self._instruments = instruments
        self._timeout = timeout_seconds
        self._ttl = catalog_ttl_seconds
        self._strict = strict_arguments
        self._discovery_timeout = min(timeout_seconds, _DISCOVERY_TIMEOUT_SECONDS)
        self._servers = {name: _ServerState() for name in self._upstreams}

    # --- catalog -------------------------------------------------------------------
    async def catalog(self) -> dict[str, CatalogEntry]:
        """Registered tools available right now. Stale servers are refreshed in the
        background (stale-while-revalidate): a slow or unreachable upstream never delays
        requests, including those for other upstreams."""
        now = time.monotonic()
        for server, state in self._servers.items():
            stale = now - state.fetched_at > self._ttl
            if stale and (state.refresh is None or state.refresh.done()):
                state.refresh = asyncio.create_task(
                    self._refresh_server(server), name=f"discover:{server}"
                )
        return self.cached_catalog()

    def cached_catalog(self) -> dict[str, CatalogEntry]:
        return {
            name: entry for state in self._servers.values() for name, entry in state.entries.items()
        }

    def input_schema(self, name: str) -> dict[str, Any] | None:
        entry = self.cached_catalog().get(name)
        return None if entry is None else entry.tool.input_schema

    def health(self) -> dict[str, str]:
        return {
            server: "ok"
            if state.error is None and state.ever_fetched
            else (state.error or "pending")
            for server, state in self._servers.items()
        }

    async def refresh(self, *, force: bool = False) -> None:
        """Refresh stale (or, with ``force``, all) servers now and wait for the result:
        used at start-up so the first requests see a populated catalog."""
        now = time.monotonic()
        await asyncio.gather(
            *(
                self._refresh_server(server)
                for server, state in self._servers.items()
                if force or now - state.fetched_at > self._ttl
            )
        )

    async def aclose(self) -> None:
        tasks = [s.refresh for s in self._servers.values() if s.refresh and not s.refresh.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _refresh_server(self, server: str) -> None:
        state = self._servers[server]
        try:
            tools = await self._list_tools(server)
        except UpstreamError as exc:
            state.error = str(exc)
            # Retry after a short pause rather than a full TTL.
            state.fetched_at = time.monotonic() - self._ttl + min(self._ttl, _RETRY_SECONDS)
            logger.warning("tool discovery failed for %s: %s", server, exc)
            return
        state.entries = self._build_entries(server, tools)
        state.fetched_at, state.error, state.ever_fetched = time.monotonic(), None, True

    async def _list_tools(self, server: str) -> list[Tool]:
        tools: list[Tool] = []
        try:
            async with self._client(
                server, GATEWAY_IDENTITY, timeout=self._discovery_timeout
            ) as client:
                cursor: str | None = None
                while True:
                    page = await client.list_tools(cursor=cursor)
                    tools.extend(page.tools)
                    cursor = page.next_cursor
                    if not cursor:
                        return tools
        except Exception as exc:
            raise UpstreamError(
                f"{server} unreachable ({type(exc).__name__})", executed="no"
            ) from exc

    def _build_entries(self, server: str, tools: list[Tool]) -> dict[str, CatalogEntry]:
        entries: dict[str, CatalogEntry] = {}
        for upstream_tool in tools:
            name = f"{server}__{upstream_tool.name}"
            spec = self._policy.tools.get(name)
            if spec is None:
                logger.info("upstream tool %s is not registered in the policy; hidden", name)
                continue
            _warn_on_hint_mismatch(name, spec, upstream_tool.annotations)
            schema = self._closed(upstream_tool.input_schema)
            try:
                validator_class = jsonschema.validators.validator_for(schema)
                validator_class.check_schema(schema)
            except jsonschema.SchemaError:
                logger.warning("upstream tool %s has an invalid input schema; hidden", name)
                continue
            exposed = _expose(name, upstream_tool, spec, schema)
            entries[name] = CatalogEntry(
                name=name,
                server=server,
                upstream_tool=upstream_tool.name,
                tool=exposed,
                validator=validator_class(schema, format_checker=jsonschema.FormatChecker()),
            )
        return entries

    def _closed(self, schema: dict[str, Any]) -> dict[str, Any]:
        if self._strict and schema.get("type") == "object" and "additionalProperties" not in schema:
            return {**schema, "additionalProperties": False}
        return schema

    # --- calls ---------------------------------------------------------------------
    async def call(
        self, entry: CatalogEntry, identity: CallerIdentity, arguments: Mapping[str, Any]
    ) -> CallToolResult:
        started = time.perf_counter()
        attributes: dict[str, str | bool] = {Attr.UPSTREAM: entry.server, Attr.TOOL: entry.name}
        try:
            async with self._client(entry.server, identity) as client:
                result = await client.call_tool(entry.upstream_tool, dict(arguments))
        except MCPError as exc:
            attributes["error"] = True
            # A protocol error means the tool did not run, unless the server itself failed.
            executed: Executed = "unknown" if exc.error.code == INTERNAL_ERROR else "no"
            raise UpstreamError(
                f"{entry.server} rejected the call: {exc.error.message}", executed=executed
            ) from exc
        except Exception as exc:
            attributes["error"] = True
            if _never_reached(exc):
                raise UpstreamError(f"{entry.server} unreachable", executed="no") from exc
            raise UpstreamError(
                f"{entry.server} did not answer ({type(exc).__name__})", executed="unknown"
            ) from exc
        else:
            attributes["error"] = result.is_error
            return result
        finally:
            self._instruments.upstream_duration.record(time.perf_counter() - started, attributes)

    @asynccontextmanager
    async def _client(
        self, server: str, identity: CallerIdentity, *, timeout: float | None = None
    ) -> AsyncIterator[Client]:
        timeout = timeout or self._timeout
        token = self._signer.sign(server, identity)
        http = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=httpx2.Timeout(timeout)
        )
        transport = streamable_http_client(self._upstreams[server], http_client=http)
        async with (
            http,
            Client(transport, read_timeout_seconds=timeout, cache=None) as client,
        ):
            yield client


def _expose(name: str, upstream: Tool, spec: ToolPolicy, schema: dict[str, Any]) -> Tool:
    hints = upstream.annotations
    description = upstream.description or ""
    if spec.requires_approval:
        description = f"{description} {APPROVAL_NOTE}".strip()
    return Tool(
        name=name,
        title=upstream.title or (hints.title if hints else None),
        description=description,
        input_schema=schema,
        output_schema=upstream.output_schema,
        annotations=ToolAnnotations(
            title=upstream.title or (hints.title if hints else None),
            read_only_hint=spec.access is AccessLevel.READ,
            destructive_hint=spec.access is AccessLevel.WRITE,
            idempotent_hint=hints.idempotent_hint if hints else None,
            open_world_hint=hints.open_world_hint if hints else None,
        ),
    )


def _warn_on_hint_mismatch(name: str, spec: ToolPolicy, hints: ToolAnnotations | None) -> None:
    if hints is None:
        return
    upstream_writes = hints.read_only_hint is False or hints.destructive_hint is True
    if spec.access is AccessLevel.READ and upstream_writes:
        logger.warning(
            "policy registers %s as read-only but the upstream marks it as writing; "
            "the policy classification is enforced: review it",
            name,
        )
