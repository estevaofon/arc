"""Stage 5 regression: MCP health tracking + cooldown + restart lifecycle.

Covers:
- Startup failure marks state="failed" with populated last_error
- Successful start marks state="healthy"
- call_tool on a failed server short-circuits with a clear message
- 3 consecutive call_tool exceptions flip healthy -> unavailable w/ 60s cooldown
- Cooldown active: call_tool returns "retry in Ns" without touching session
- Cooldown expired: enters half-open. Success -> healthy; failure -> unavailable
  with doubled (120s) cooldown
- get_catalog_text omits tools from non-healthy servers and adds a note
- restart_server removes catalog entries during the window so callers don't
  see half-published state
"""

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from aru.tools.mcp_client import (
    _COOLDOWN_SECS,
    _COOLDOWN_SECS_BACKOFF,
    _FAILURE_THRESHOLD,
    McpServerHealth,
    McpSessionManager,
    McpToolEntry,
)


def _make_entry(server_name: str, tool_name: str = "do_thing", session=None):
    return McpToolEntry(
        name=f"{server_name}__{tool_name}",
        description=f"[{server_name}] desc",
        parameters={"type": "object", "properties": {}},
        server_name=server_name,
        original_name=tool_name,
        session=session or MagicMock(),
    )


@pytest.mark.asyncio
async def test_start_server_failure_marks_failed(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({
        "mcpServers": {"broken": {"command": "mcp-server-que-nao-existe"}}
    }))
    mgr = McpSessionManager(config_path=str(cfg))
    with patch("aru.tools.mcp_client.stdio_client", side_effect=FileNotFoundError("no binary")):
        await mgr.initialize()
    assert mgr.health["broken"].state == "failed"
    assert "no binary" in mgr.health["broken"].last_error
    assert mgr.health["broken"].last_error_at > 0


@pytest.mark.asyncio
async def test_call_tool_on_failed_server_short_circuits():
    mgr = McpSessionManager()
    mgr.health["broken"] = McpServerHealth(
        name="broken", state="failed", last_error="ENOENT"
    )
    mgr.catalog["broken__foo"] = _make_entry("broken", "foo")
    result = await mgr.call_tool("broken__foo", {})
    assert "failed at startup" in result
    assert "ENOENT" in result
    assert "/mcp restart broken" in result


@pytest.mark.asyncio
async def test_three_consecutive_failures_trip_breaker():
    mgr = McpSessionManager()
    mgr.health["s"] = McpServerHealth(name="s", state="healthy")

    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=RuntimeError("transient"))
    mgr.catalog["s__x"] = _make_entry("s", "x", session=session)

    for _ in range(_FAILURE_THRESHOLD):
        await mgr.call_tool("s__x", {})

    h = mgr.health["s"]
    assert h.state == "unavailable"
    assert h.consecutive_failures == _FAILURE_THRESHOLD
    assert h.cooldown_until > time.time()
    # Cooldown is around the configured window (allow a second of slack)
    assert h.cooldown_until - time.time() > _COOLDOWN_SECS - 2


@pytest.mark.asyncio
async def test_cooldown_active_returns_retry_message_without_calling_session():
    mgr = McpSessionManager()
    mgr.health["s"] = McpServerHealth(
        name="s",
        state="unavailable",
        cooldown_until=time.time() + 45,
        last_error="earlier boom",
    )
    session = MagicMock()
    session.call_tool = AsyncMock()
    mgr.catalog["s__x"] = _make_entry("s", "x", session=session)

    result = await mgr.call_tool("s__x", {})
    assert "unavailable" in result
    assert "retry in" in result
    assert "earlier boom" in result
    session.call_tool.assert_not_called()


@pytest.mark.asyncio
async def test_half_open_success_returns_to_healthy():
    mgr = McpSessionManager()
    mgr.health["s"] = McpServerHealth(
        name="s",
        state="unavailable",
        cooldown_until=time.time() - 1,  # expired
        consecutive_failures=_FAILURE_THRESHOLD,
    )
    session = MagicMock()
    tool_result = MagicMock()
    tool_result.content = []
    tool_result.isError = False
    session.call_tool = AsyncMock(return_value=tool_result)
    mgr.catalog["s__x"] = _make_entry("s", "x", session=session)

    await mgr.call_tool("s__x", {})
    h = mgr.health["s"]
    assert h.state == "healthy"
    assert h.consecutive_failures == 0
    assert h.last_success_at > 0


@pytest.mark.asyncio
async def test_half_open_failure_doubles_cooldown():
    mgr = McpSessionManager()
    mgr.health["s"] = McpServerHealth(
        name="s",
        state="unavailable",
        cooldown_until=time.time() - 1,  # expired -> half-open on next call
        consecutive_failures=_FAILURE_THRESHOLD,
    )
    session = MagicMock()
    session.call_tool = AsyncMock(side_effect=RuntimeError("still broken"))
    mgr.catalog["s__x"] = _make_entry("s", "x", session=session)

    await mgr.call_tool("s__x", {})
    h = mgr.health["s"]
    assert h.state == "unavailable"
    assert h.cooldown_until - time.time() > _COOLDOWN_SECS_BACKOFF - 2


@pytest.mark.asyncio
async def test_get_catalog_text_hides_unhealthy_servers():
    mgr = McpSessionManager()
    # healthy server with one tool
    mgr.health["good"] = McpServerHealth(name="good", state="healthy")
    mgr.catalog["good__a"] = _make_entry("good", "a")
    # failed server with one tool — should be hidden
    mgr.health["bad"] = McpServerHealth(
        name="bad", state="failed", last_error="binary missing"
    )
    mgr.catalog["bad__b"] = _make_entry("bad", "b")

    text = mgr.get_catalog_text()
    assert "good__a" in text
    assert "bad__b" not in text
    assert "bad" in text  # mentioned in availability notes
    assert "failed" in text
    assert "/mcp restart bad" in text


@pytest.mark.asyncio
async def test_get_catalog_text_empty_when_nothing_configured():
    mgr = McpSessionManager()
    assert mgr.get_catalog_text() == ""


@pytest.mark.asyncio
async def test_restart_unknown_server_returns_error():
    mgr = McpSessionManager()
    result = await mgr.restart_server("does-not-exist")
    assert "Unknown MCP server" in result


@pytest.mark.asyncio
async def test_restart_clears_catalog_before_reconnect():
    """While `_start_server` is running, old catalog entries must not be visible."""
    mgr = McpSessionManager()
    mgr._server_configs["s"] = {"command": "fake"}
    mgr.health["s"] = McpServerHealth(name="s", state="healthy")
    mgr.catalog["s__old"] = _make_entry("s", "old")

    captured_state: dict = {}

    async def fake_start(name, cfg):
        # Inside _start_server — verify the old catalog entries are gone
        captured_state["catalog_keys"] = list(mgr.catalog.keys())
        captured_state["state"] = mgr.health[name].state
        # Simulate a failure so the test doesn't need a full session mock
        mgr.health[name].state = "failed"
        mgr.health[name].last_error = "mocked"

    mgr._start_server = fake_start  # type: ignore[assignment]

    result = await mgr.restart_server("s")
    assert "restart failed" in result
    assert captured_state["catalog_keys"] == []  # no half-published entries
    assert captured_state["state"] == "initializing"


@pytest.mark.asyncio
async def test_transport_error_tool_result_does_not_trip_breaker():
    """A server returning isError=True is a normal tool error, not a breaker trip."""
    mgr = McpSessionManager()
    mgr.health["s"] = McpServerHealth(name="s", state="healthy")
    session = MagicMock()
    content = MagicMock()
    content.text = "tool-level error"
    tool_result = MagicMock()
    tool_result.content = [content]
    tool_result.isError = True
    session.call_tool = AsyncMock(return_value=tool_result)
    mgr.catalog["s__x"] = _make_entry("s", "x", session=session)

    for _ in range(5):
        await mgr.call_tool("s__x", {})

    assert mgr.health["s"].state == "healthy"
    assert mgr.health["s"].consecutive_failures == 0


@pytest.mark.asyncio
async def test_call_unknown_tool_returns_friendly_error():
    mgr = McpSessionManager()
    result = await mgr.call_tool("nonexistent__tool", {})
    assert "Unknown MCP tool" in result
