"""Verify ``delegate_task`` publishes ``subagent.tool.{started,completed}``.

These events power the TUI's SubagentPanel — without them the panel
shows only "spawn → done" with nothing in between. The test feeds a
fake Agno-compatible agent that yields real ``ToolCallStartedEvent`` /
``ToolCallCompletedEvent`` / ``RunOutput`` instances through delegate's
streaming loop, then asserts the captured plugin-bus payloads carry
the right shape.

Also pins:
* the cancel branch emits a ``subagent.complete`` with ``status=cancelled``;
* the error branch emits a ``subagent.complete`` with ``status=error``.

Both used to leave the panel's row stuck in "running" forever — the
fix landed alongside the gap-5 implementation.
"""

from __future__ import annotations

import asyncio

import pytest


pytest.importorskip("agno")


# ── Test harness ─────────────────────────────────────────────────────


def _setup_ctx_with_bus():
    """Initialise a runtime ctx with a loaded PluginManager + bus capture.

    Returns ``(captured_events_dict, plugin_mgr)``. The caller adds whatever
    ``subscribe`` they need; the dict gives a single place to look up
    payloads by event_type after publishes settle.
    """
    from aru.permissions import (
        PermissionConfig,
        reset_session,
        set_config,
        set_skip_permissions,
    )
    from aru.plugins.manager import PluginManager
    from aru.runtime import get_ctx, init_ctx, reset_abort
    from aru.session import Session

    init_ctx()
    ctx = get_ctx()
    ctx.session = Session()
    set_config(PermissionConfig())
    reset_session()
    set_skip_permissions(True)
    reset_abort()

    mgr = PluginManager()
    mgr._loaded = True
    ctx.plugin_manager = mgr
    return mgr


def _make_started(tool_call_id: str, tool_name: str, tool_args: dict | None = None):
    from agno.models.response import ToolExecution
    from agno.run.agent import ToolCallStartedEvent

    return ToolCallStartedEvent(
        tool=ToolExecution(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_args=tool_args or {},
        ),
    )


def _make_completed(tool_call_id: str, tool_name: str, tool_args: dict | None = None):
    from agno.models.response import ToolExecution
    from agno.run.agent import ToolCallCompletedEvent

    return ToolCallCompletedEvent(
        tool=ToolExecution(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            tool_args=tool_args or {},
        ),
    )


class _StreamingFakeAgent:
    """Yields a programmable sequence of Agno events through arun().

    The script is a list of (kind, *payload) tuples evaluated in order:
        ("started", id, name, args)
        ("completed", id, name, args)
        ("output", text)
    """

    def __init__(self, name: str, script: list[tuple]):
        self.name = name
        self._script = script
        self.model = None
        self.tools = []
        self.instructions = ""

    async def arun(self, task, stream=True, stream_events=True, yield_run_output=True):
        from agno.run.agent import RunOutput

        for step in self._script:
            kind = step[0]
            if kind == "started":
                yield _make_started(*step[1:])
            elif kind == "completed":
                yield _make_completed(*step[1:])
            elif kind == "output":
                yield RunOutput(content=step[1])
            else:
                raise ValueError(f"Unknown script step: {kind}")


async def _wait_publishes(*, max_iters: int = 5):
    """Pump the loop a few times so create_task'd publishes drain."""
    for _ in range(max_iters):
        await asyncio.sleep(0)


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delegate_emits_tool_started_for_each_tool_call(monkeypatch):
    mgr = _setup_ctx_with_bus()
    captured: list[dict] = []
    mgr.subscribe("subagent.tool.started", lambda p: captured.append(p))

    fake = _StreamingFakeAgent(
        name="Explorer-9",
        script=[
            ("started", "tc-1", "grep_search", {"pattern": "delegate"}),
            ("completed", "tc-1", "grep_search", {"pattern": "delegate"}),
            ("started", "tc-2", "read_file", {"path": "x.py"}),
            ("completed", "tc-2", "read_file", {"path": "x.py"}),
            ("output", "summary text"),
        ],
    )

    async def _fake_create_from_spec(*a, **kw):
        return fake

    monkeypatch.setattr(
        "aru.agent_factory.create_agent_from_spec", _fake_create_from_spec
    )

    from aru.tools import delegate as delegate_mod
    result = await delegate_mod.delegate_task("hunt", agent_name="explorer")
    await _wait_publishes()

    assert "summary text" in result
    # Two tool starts captured, in order.
    assert [c["tool_name"] for c in captured] == ["grep_search", "read_file"]
    assert all(c["task_id"] for c in captured), "task_id must be set on every event"
    # tool_args_preview is the truncated repr of the args dict.
    assert "delegate" in captured[0]["tool_args_preview"]


@pytest.mark.asyncio
async def test_delegate_emits_tool_completed_with_duration(monkeypatch):
    mgr = _setup_ctx_with_bus()
    captured: list[dict] = []
    mgr.subscribe("subagent.tool.completed", lambda p: captured.append(p))

    fake = _StreamingFakeAgent(
        name="Verifier-2",
        script=[
            ("started", "tc-1", "read_file", {"path": "y.py"}),
            ("completed", "tc-1", "read_file", {"path": "y.py"}),
            ("output", "ok"),
        ],
    )

    async def _fake_create_from_spec(*a, **kw):
        return fake

    monkeypatch.setattr(
        "aru.agent_factory.create_agent_from_spec", _fake_create_from_spec
    )

    from aru.tools import delegate as delegate_mod
    await delegate_mod.delegate_task("verify", agent_name="verification")
    await _wait_publishes()

    assert len(captured) == 1
    assert captured[0]["tool_name"] == "read_file"
    assert "duration_ms" in captured[0]
    # Even an instant fake call has a non-negative duration.
    assert captured[0]["duration_ms"] >= 0
    assert captured[0]["error"] is None


@pytest.mark.asyncio
async def test_started_and_completed_share_task_id(monkeypatch):
    """Same subagent instance → same task_id across started/completed pairs."""
    mgr = _setup_ctx_with_bus()
    started: list[dict] = []
    completed: list[dict] = []
    mgr.subscribe("subagent.tool.started", lambda p: started.append(p))
    mgr.subscribe("subagent.tool.completed", lambda p: completed.append(p))

    fake = _StreamingFakeAgent(
        name="Explorer-1",
        script=[
            ("started", "tc-A", "grep_search"),
            ("completed", "tc-A", "grep_search"),
            ("output", "done"),
        ],
    )

    async def _fake_create_from_spec(*a, **kw):
        return fake

    monkeypatch.setattr(
        "aru.agent_factory.create_agent_from_spec", _fake_create_from_spec
    )

    from aru.tools import delegate as delegate_mod
    await delegate_mod.delegate_task("research", agent_name="explorer")
    await _wait_publishes()

    assert started and completed
    assert started[0]["task_id"] == completed[0]["task_id"]


@pytest.mark.asyncio
async def test_cancel_branch_emits_complete_event(monkeypatch):
    """Ctrl+C path used to leave the SubagentPanel row spinning forever."""
    mgr = _setup_ctx_with_bus()
    completes: list[dict] = []
    mgr.subscribe("subagent.complete", lambda p: completes.append(p))

    from aru.runtime import get_ctx
    # Pre-set the abort flag so the very first iteration of the streaming
    # loop short-circuits into the cancel branch — no need to race a real
    # signal with the loop.
    get_ctx().abort_event.set()

    fake = _StreamingFakeAgent(
        name="Explorer-3",
        script=[("output", "should not be reached")],
    )

    async def _fake_create_from_spec(*a, **kw):
        return fake

    monkeypatch.setattr(
        "aru.agent_factory.create_agent_from_spec", _fake_create_from_spec
    )

    from aru.tools import delegate as delegate_mod
    result = await delegate_mod.delegate_task("...", agent_name="explorer")
    await _wait_publishes()

    assert "Cancelled by user" in result
    assert any(c.get("status") == "cancelled" for c in completes), (
        f"expected cancelled subagent.complete, got: {completes}"
    )


@pytest.mark.asyncio
async def test_error_branch_emits_complete_event(monkeypatch):
    """Exception inside the stream must surface a complete event with error."""
    mgr = _setup_ctx_with_bus()
    completes: list[dict] = []
    mgr.subscribe("subagent.complete", lambda p: completes.append(p))

    class _BoomAgent:
        name = "Explorer-Boom"
        model = None
        tools = []
        instructions = ""

        async def arun(self, task, stream=True, stream_events=True,
                       yield_run_output=True):
            yield _make_started("tc-1", "read_file", {"path": "x"})
            raise RuntimeError("kaboom")

    fake = _BoomAgent()

    async def _fake_create_from_spec(*a, **kw):
        return fake

    monkeypatch.setattr(
        "aru.agent_factory.create_agent_from_spec", _fake_create_from_spec
    )

    from aru.tools import delegate as delegate_mod
    # delegate_task swallows exceptions and surfaces them as text — the
    # retry path also fires, but an error event lands on the bus first.
    await delegate_mod.delegate_task("..", agent_name="explorer")
    await _wait_publishes()

    assert any(c.get("status") == "error" for c in completes), (
        f"expected error subagent.complete, got: {completes}"
    )
