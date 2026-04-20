"""Model Context Protocol (MCP) client manager and tool generation.

Supports two modes for exposing MCP tools to agents:
- **Eager** (legacy): Each MCP tool becomes its own Agno Function with full JSON Schema.
  Sends all tool schemas in every request — expensive with many tools.
- **Lazy** (default): A single gateway tool `use_mcp_tool` replaces all individual tools.
  The tool catalog (name + description) is injected as lightweight text in the system prompt.
  Full schema resolution happens only when the model invokes a specific tool.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Literal

from agno.tools import Function
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession


# -- Health tracking (Stage 5) ---------------------------------------------

HealthState = Literal["healthy", "initializing", "failed", "unavailable", "cooldown"]

# Threshold of consecutive call_tool failures before a "healthy" server is
# demoted to "unavailable" with a cooldown window.
_FAILURE_THRESHOLD = 3
# Initial cooldown after tripping from healthy -> unavailable.
_COOLDOWN_SECS = 60.0
# Cooldown after the half-open retry also fails (backoff doubled).
_COOLDOWN_SECS_BACKOFF = 120.0


@dataclass
class McpServerHealth:
    """Live state of a single MCP server subprocess.

    Two failure modes are distinguished so recovery UX differs:
    - ``failed``:       server never came up (binary missing, config error).
                        Only ``/mcp restart`` recovers; no lazy retry.
    - ``unavailable``:  server started fine but call_tool failed >=3x
                        consecutively. Cooldown-gated half-open retry
                        returns to ``healthy`` on success, or to
                        ``unavailable`` with doubled cooldown on fail.
    """

    name: str
    state: HealthState = "initializing"
    last_error: str = ""
    last_error_at: float = 0.0
    last_success_at: float = 0.0
    consecutive_failures: int = 0
    cooldown_until: float = 0.0


@dataclass
class McpToolEntry:
    """Lightweight catalog entry for a discovered MCP tool."""
    name: str           # safe_name: "server__tool_name"
    description: str    # "[server] original description"
    parameters: dict    # full JSON Schema (only used on invocation)
    server_name: str    # originating MCP server
    original_name: str  # tool name as the MCP server knows it
    session: ClientSession = field(repr=False)


class McpSessionManager:
    """Manages MCP server subprocesses and active client sessions."""

    def __init__(self, config_path: str = "arc.mcp.json"):
        self.config_path = config_path
        self._exit_stack = AsyncExitStack()
        self.sessions: dict[str, ClientSession] = {}
        self.catalog: dict[str, McpToolEntry] = {}
        # Per-server health. Populated by `_start_server` / `_fetch` and
        # mutated by `call_tool` as the circuit-breaker fires.
        self.health: dict[str, McpServerHealth] = {}
        # Config snapshot kept so `/mcp restart <name>` can re-invoke
        # `_start_server` without re-reading the file.
        self._server_configs: dict[str, dict] = {}
        # Serialises `/mcp restart`: catalog removal + reconnect + refetch
        # must be atomic vs a concurrent `call_tool` to avoid the model
        # seeing half-published catalog entries.
        self._manager_lock = asyncio.Lock()

    async def initialize(self):
        """Read config and spawn all MCP servers concurrently."""
        if not os.path.exists(self.config_path):
            return

        with open(self.config_path, "r", encoding="utf-8") as f:
            try:
                config = json.load(f)
            except json.JSONDecodeError:
                print(f"[Warning] Failed to parse {self.config_path}")
                return

        servers = config.get("mcpServers", {})
        tasks = []
        for name, svr_config in servers.items():
            cmd = svr_config.get("command")
            if not cmd:
                continue
            self._server_configs[name] = svr_config
            tasks.append(self._start_server(name, svr_config))

        if tasks:
            await asyncio.gather(*tasks)

    async def _start_server(self, name: str, svr_config: dict):
        """Start a single MCP server and register its session.

        Updates ``self.health[name]`` to ``initializing`` before the attempt,
        ``healthy`` on success, or ``failed`` with a populated last_error on
        exception. Does not raise — MCP startup failures are expected (e.g.
        binary missing) and other servers should continue initialising.
        """
        cmd = svr_config.get("command")
        args = svr_config.get("args", [])
        env = svr_config.get("env", None)

        self.health[name] = McpServerHealth(name=name, state="initializing")

        server_params = StdioServerParameters(
            command=cmd,
            args=args,
            env={**os.environ.copy(), **env} if env else None
        )

        try:
            read_stream, write_stream = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            session = await self._exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )

            await session.initialize()
            self.sessions[name] = session
            self.health[name].state = "healthy"
            self.health[name].last_success_at = time.time()
        except Exception as e:
            self.health[name].state = "failed"
            self.health[name].last_error = str(e)
            self.health[name].last_error_at = time.time()
            print(f"[Warning] Failed to start MCP server '{name}': {e}")

    async def discover_tools(self) -> int:
        """Fetch all tools from connected servers and populate the catalog.

        Returns the number of tools discovered.
        """
        async def _fetch(server_name: str, session: ClientSession) -> list[McpToolEntry]:
            try:
                result = await session.list_tools()
                entries = []
                for tool in result.tools:
                    safe_name = f"{server_name}__{tool.name}".replace("-", "_")
                    entries.append(McpToolEntry(
                        name=safe_name,
                        description=f"[{server_name}] {tool.description or ''}",
                        parameters=tool.inputSchema,
                        server_name=server_name,
                        original_name=tool.name,
                        session=session,
                    ))
                return entries
            except Exception as e:
                print(f"[Warning] Failed to fetch tools from MCP server '{server_name}': {e}")
                return []

        results = await asyncio.gather(
            *[_fetch(name, sess) for name, sess in self.sessions.items()]
        )
        for entries in results:
            for entry in entries:
                self.catalog[entry.name] = entry

        return len(self.catalog)

    async def call_tool(self, tool_name: str, arguments: dict | None = None) -> str:
        """Execute an MCP tool by its safe name.

        Circuit-breaker transitions:

        - ``healthy``: try the call. Success resets counters. 3 consecutive
          failures -> ``unavailable`` with a 60s cooldown.
        - ``unavailable`` with cooldown active: return an immediate
          "retry in Ns" message without touching the session.
        - ``unavailable`` with cooldown expired: half-open retry (a single
          attempt). Success -> ``healthy``. Failure -> ``unavailable``
          with a doubled 120s cooldown.
        - ``failed`` (startup): short-circuit immediately. Only
          ``/mcp restart`` recovers.
        - ``initializing``: a concurrent ``/mcp restart`` is mid-flight;
          ask the caller to retry shortly.
        """
        entry = self.catalog.get(tool_name)
        if entry is None:
            available = ", ".join(sorted(self.catalog.keys()))
            return f"Error: Unknown MCP tool '{tool_name}'. Available: {available}"

        health = self.health.get(entry.server_name)
        if health is not None:
            if health.state == "failed":
                return (
                    f"Error: MCP server '{entry.server_name}' failed at startup: "
                    f"{health.last_error}. Use /mcp restart {entry.server_name} or "
                    f"fall back to local tools."
                )
            if health.state == "initializing":
                return (
                    f"Error: MCP server '{entry.server_name}' is restarting. "
                    f"Retry in a moment."
                )
            if health.state == "unavailable":
                now = time.time()
                if now < health.cooldown_until:
                    remaining = int(health.cooldown_until - now)
                    return (
                        f"Error: MCP server '{entry.server_name}' unavailable "
                        f"(retry in {remaining}s). Previous error: "
                        f"{health.last_error}. Use /mcp restart {entry.server_name} "
                        f"or fall back to local tools."
                    )
                # Cooldown expired: enter half-open retry.
                health.state = "cooldown"

        try:
            result = await entry.session.call_tool(entry.original_name, arguments=arguments or {})
            output = []
            for content in result.content:
                if hasattr(content, "text"):
                    output.append(content.text)
            if result.isError:
                # Tool error is a "normal" failure from the server's POV —
                # the call succeeded at the transport level. Don't trip the
                # breaker; just pass through.
                return f"Error from {entry.original_name}: " + "\n".join(output)
            # Success: reset counters
            if health is not None:
                health.state = "healthy"
                health.consecutive_failures = 0
                health.last_success_at = time.time()
            return "\n".join(output)
        except Exception as e:
            if health is not None:
                health.last_error = str(e)
                health.last_error_at = time.time()
                # Half-open -> unavailable with doubled cooldown
                if health.state == "cooldown":
                    health.state = "unavailable"
                    health.cooldown_until = time.time() + _COOLDOWN_SECS_BACKOFF
                else:
                    health.consecutive_failures += 1
                    if health.consecutive_failures >= _FAILURE_THRESHOLD:
                        health.state = "unavailable"
                        health.cooldown_until = time.time() + _COOLDOWN_SECS
            return f"Error executing {entry.original_name} on {entry.server_name}: {e}"

    def get_catalog_text(self) -> str:
        """Build a lightweight text catalog of available MCP tools.

        This text is injected into the system prompt so the model knows
        which tools exist — without the cost of full JSON Schema per tool.
        Entries whose owning server is not ``healthy`` are omitted so the
        model doesn't hallucinate calls into a dead gateway; a footer notes
        which servers are degraded so the agent can explain the gap.
        """
        # Group by server, including only servers currently healthy.
        by_server: dict[str, list[McpToolEntry]] = {}
        for entry in self.catalog.values():
            health = self.health.get(entry.server_name)
            if health is None or health.state == "healthy":
                by_server.setdefault(entry.server_name, []).append(entry)

        degraded = [h for h in self.health.values() if h.state != "healthy"]

        if not by_server and not degraded:
            return ""

        lines: list[str] = []
        if by_server:
            lines.append("## MCP Tools (external)\n")
            lines.append("Call these via `use_mcp_tool(tool_name=\"<name>\", arguments={...})`.\n")
            for server, entries in sorted(by_server.items()):
                lines.append(f"### {server}")
                for entry in sorted(entries, key=lambda e: e.name):
                    desc = entry.description.split("] ", 1)[-1] if "] " in entry.description else entry.description
                    # Include parameter names as hints
                    props = entry.parameters.get("properties", {})
                    if props:
                        param_hints = ", ".join(props.keys())
                        lines.append(f"- `{entry.name}({param_hints})`: {desc}")
                    else:
                        lines.append(f"- `{entry.name}()`: {desc}")
                lines.append("")

        if degraded:
            lines.append("### MCP availability notes")
            for h in sorted(degraded, key=lambda x: x.name):
                tool_count = sum(1 for e in self.catalog.values() if e.server_name == h.name)
                if h.state == "failed":
                    lines.append(
                        f"- `{h.name}` failed to start — {tool_count} tool(s) unavailable. "
                        f"Use `/mcp restart {h.name}`."
                    )
                elif h.state == "unavailable":
                    remaining = max(0, int(h.cooldown_until - time.time()))
                    lines.append(
                        f"- `{h.name}` unavailable (cooldown {remaining}s, "
                        f"{tool_count} tool(s) hidden). Use `/mcp restart {h.name}`."
                    )
                elif h.state == "initializing":
                    lines.append(f"- `{h.name}` restarting — retry shortly.")
            lines.append("")

        return "\n".join(lines)

    async def restart_server(self, name: str) -> str:
        """Atomically restart a single MCP server.

        Lifecycle: acquire manager lock -> mark initializing -> drop catalog
        entries for that server -> open fresh session -> rebuild catalog
        on success. Any ``call_tool`` fired while the lock is held sees
        ``state == "initializing"`` and returns a transient error.
        """
        async with self._manager_lock:
            cfg = self._server_configs.get(name)
            if cfg is None:
                return f"Unknown MCP server: {name}"

            # Mark initializing + evict stale catalog entries BEFORE reconnect
            # so no half-published entries are visible to call_tool.
            self.health[name] = McpServerHealth(name=name, state="initializing")
            for key in [k for k, e in self.catalog.items() if e.server_name == name]:
                self.catalog.pop(key, None)
            self.sessions.pop(name, None)

            await self._start_server(name, cfg)
            health = self.health.get(name)
            if health is None or health.state != "healthy":
                err = health.last_error if health else "unknown error"
                return f"MCP server '{name}' restart failed: {err}"

            # Refetch tools for the freshly-started server.
            session = self.sessions.get(name)
            if session is None:
                return f"MCP server '{name}' has no active session after restart"

            try:
                result = await session.list_tools()
                for tool in result.tools:
                    safe_name = f"{name}__{tool.name}".replace("-", "_")
                    self.catalog[safe_name] = McpToolEntry(
                        name=safe_name,
                        description=f"[{name}] {tool.description or ''}",
                        parameters=tool.inputSchema,
                        server_name=name,
                        original_name=tool.name,
                        session=session,
                    )
                return f"MCP server '{name}' restarted ({len(result.tools)} tool(s) reloaded)."
            except Exception as e:
                self.health[name].state = "failed"
                self.health[name].last_error = str(e)
                self.health[name].last_error_at = time.time()
                return f"MCP server '{name}' restarted but tool discovery failed: {e}"

    def get_eager_tools(self) -> list[Function]:
        """Create individual Agno Functions for each MCP tool (legacy eager mode)."""
        functions = []
        for entry in self.catalog.values():
            async def mcp_caller(*, _entry=entry, **kwargs) -> str:
                return await self.call_tool(_entry.name, kwargs)

            mcp_caller.__name__ = entry.name

            functions.append(Function(
                name=entry.name,
                description=entry.description,
                parameters=entry.parameters,
                entrypoint=mcp_caller,
            ))
        return functions

    # -- Backward-compatible API (used by tests and eager mode) --

    async def get_tools(self) -> list[Function]:
        """Fetch tools and return as Agno Functions (legacy API).

        Calls discover_tools() if catalog is empty, then returns eager functions.
        """
        if not self.catalog:
            await self.discover_tools()
        return self.get_eager_tools()

    def _create_agno_function(self, server_name: str, session: ClientSession, tool) -> Function:
        """Create a single Agno Function from an MCP tool (legacy API)."""
        safe_name = f"{server_name}__{tool.name}".replace("-", "_")
        description = f"[{server_name}] {tool.description or ''}"
        original_name = tool.name

        async def mcp_caller(**kwargs) -> str:
            try:
                result = await session.call_tool(original_name, arguments=kwargs)
                output = []
                for content in result.content:
                    if hasattr(content, "text"):
                        output.append(content.text)
                if result.isError:
                    return f"Error from {original_name}: " + "\n".join(output)
                return "\n".join(output)
            except Exception as e:
                return f"Error executing {original_name} on {server_name}: {e}"

        mcp_caller.__name__ = safe_name

        return Function(
            name=safe_name,
            description=description,
            parameters=tool.inputSchema,
            entrypoint=mcp_caller,
        )

    async def cleanup(self):
        """Close all active MCP client sessions and terminate server subprocesses."""
        try:
            await self._exit_stack.aclose()
        except (RuntimeError, Exception):
            pass


# Global Singleton manager to be used entirely inside aru's async loops
_manager: McpSessionManager | None = None


async def init_mcp() -> McpSessionManager | None:
    """Initialize MCP servers, discover tools, and return the manager.

    Returns None if no MCP config is found.
    """
    global _manager
    if _manager is None:
        config_path = None
        for path in [
            ".aru/mcp_servers.json",
            "aru.mcp.json",
            ".mcp.json",
            "mcp.json"
        ]:
            if os.path.exists(path):
                config_path = path
                break

        if config_path:
            _manager = McpSessionManager(config_path=config_path)
            await _manager.initialize()
            await _manager.discover_tools()
        else:
            _manager = McpSessionManager(config_path="")
            return None

    return _manager


def get_mcp_manager() -> McpSessionManager | None:
    """Return the global MCP manager (None if not initialized)."""
    return _manager


async def cleanup_mcp():
    """Cleanup global manager."""
    global _manager
    if _manager:
        await _manager.cleanup()
        _manager = None
