"""SubagentPanel — live view of running sub-agents in the TUI.

Pinning the contract:
* hidden when no sub-agent is active (``-busy`` class absent);
* one row per ``subagent.start``, displayed agent name + initial spinner;
* row label updates on ``subagent.tool.started`` and clears back to
  "thinking…" on ``subagent.tool.completed``;
* row marks done (✓ / ✗ / ⊘) on ``subagent.complete``;
* completed rows fade out after ``FADE_SECONDS`` and the panel returns
  to ``display: none`` when the last row is reaped;
* color is deterministic per ``agent_name`` so the same sub-agent keeps
  the same color across its lifetime.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("textual")


# ── Color helper (pure function, tested independently of the App) ────


def test_color_for_is_deterministic_and_stable():
    from aru.tui.widgets.subagent_panel import _color_for

    a1 = _color_for("Explorer-3")
    a2 = _color_for("Explorer-3")
    assert a1 == a2

    # Different names land on (likely) different palette slots — the
    # palette has 8 entries, so collisions are possible but should not
    # happen for these specific strings (verified empirically).
    b = _color_for("Explorer-4")
    c = _color_for("Verifier-7")
    # At least one of the three pairs must differ — guards against an
    # accidental "always returns palette[0]" regression.
    assert len({a1, b, c}) >= 2


def test_color_for_handles_empty_name():
    from aru.tui.widgets.subagent_panel import _color_for

    # Empty string used to crash an early draft that hashed before the
    # truthiness check — pin the safe fallback.
    assert _color_for("") == "cyan"


def test_fmt_dur_thresholds():
    from aru.tui.widgets.subagent_panel import _fmt_dur

    assert _fmt_dur(0.32) == "320ms"
    assert _fmt_dur(1.4) == "1.4s"
    assert _fmt_dur(127.0) == "2m07s"


# ── App-level integration via the plugin bus ────────────────────────


@pytest.mark.asyncio
async def test_panel_hidden_when_idle():
    from aru.plugins.manager import PluginManager
    from aru.tui.app import AruApp
    from aru.tui.widgets.subagent_panel import SubagentPanel

    plugin_mgr = PluginManager()
    plugin_mgr._loaded = True
    app = AruApp(plugin_manager=plugin_mgr)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(SubagentPanel)
        assert not panel.has_class("-busy")
        assert panel.active_task_ids() == []


@pytest.mark.asyncio
async def test_panel_appears_on_subagent_start():
    from aru.plugins.manager import PluginManager
    from aru.tui.app import AruApp
    from aru.tui.widgets.subagent_panel import SubagentPanel

    plugin_mgr = PluginManager()
    plugin_mgr._loaded = True
    app = AruApp(plugin_manager=plugin_mgr)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(SubagentPanel)

        await plugin_mgr.publish(
            "subagent.start",
            {"task_id": "sa-1", "agent_name": "Explorer-1", "parent_id": None,
             "task": "find calls to delegate_task"},
        )
        await pilot.pause()
        assert panel.has_class("-busy")
        assert panel.active_task_ids() == ["sa-1"]
        # Row stores the agent name + initial running state.
        row = panel._rows["sa-1"]
        assert row["agent"] == "Explorer-1"
        assert row["status"] == "running"
        assert row["done"] is False


@pytest.mark.asyncio
async def test_panel_updates_current_tool():
    from aru.plugins.manager import PluginManager
    from aru.tui.app import AruApp
    from aru.tui.widgets.subagent_panel import SubagentPanel

    plugin_mgr = PluginManager()
    plugin_mgr._loaded = True
    app = AruApp(plugin_manager=plugin_mgr)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(SubagentPanel)
        await plugin_mgr.publish(
            "subagent.start",
            {"task_id": "sa-1", "agent_name": "Explorer-1", "parent_id": None},
        )
        await plugin_mgr.publish(
            "subagent.tool.started",
            {"task_id": "sa-1", "tool_id": "t1",
             "tool_name": "grep_search", "tool_args_preview": "{'pattern': 'foo'}"},
        )
        await pilot.pause()
        assert panel._rows["sa-1"]["current_tool"] == "grep_search"
        assert "foo" in panel._rows["sa-1"]["tool_args"]

        await plugin_mgr.publish(
            "subagent.tool.completed",
            {"task_id": "sa-1", "tool_id": "t1", "tool_name": "grep_search",
             "duration_ms": 120.0, "error": None},
        )
        await pilot.pause()
        # Cleared until the next tool fires — row reads "thinking…" via
        # the renderer.
        assert panel._rows["sa-1"]["current_tool"] == ""


@pytest.mark.asyncio
async def test_panel_marks_done_on_complete():
    from aru.plugins.manager import PluginManager
    from aru.tui.app import AruApp
    from aru.tui.widgets.subagent_panel import SubagentPanel

    plugin_mgr = PluginManager()
    plugin_mgr._loaded = True
    app = AruApp(plugin_manager=plugin_mgr)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(SubagentPanel)
        await plugin_mgr.publish(
            "subagent.start",
            {"task_id": "sa-1", "agent_name": "Explorer-1", "parent_id": None},
        )
        await plugin_mgr.publish(
            "subagent.complete",
            {"task_id": "sa-1", "status": "completed",
             "tokens_in": 1200, "tokens_out": 340, "duration": 2.5},
        )
        await pilot.pause()
        row = panel._rows["sa-1"]
        assert row["done"] is True
        assert row["status"] == "completed"
        assert row["tokens_in"] == 1200
        assert row["tokens_out"] == 340


@pytest.mark.asyncio
async def test_panel_marks_error_status():
    from aru.plugins.manager import PluginManager
    from aru.tui.app import AruApp
    from aru.tui.widgets.subagent_panel import SubagentPanel

    plugin_mgr = PluginManager()
    plugin_mgr._loaded = True
    app = AruApp(plugin_manager=plugin_mgr)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(SubagentPanel)
        await plugin_mgr.publish(
            "subagent.start",
            {"task_id": "sa-2", "agent_name": "Verifier-1"},
        )
        await plugin_mgr.publish(
            "subagent.complete",
            {"task_id": "sa-2", "status": "error"},
        )
        await pilot.pause()
        assert panel._rows["sa-2"]["status"] == "error"


@pytest.mark.asyncio
async def test_panel_handles_two_concurrent():
    from aru.plugins.manager import PluginManager
    from aru.tui.app import AruApp
    from aru.tui.widgets.subagent_panel import SubagentPanel

    plugin_mgr = PluginManager()
    plugin_mgr._loaded = True
    app = AruApp(plugin_manager=plugin_mgr)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(SubagentPanel)
        await plugin_mgr.publish(
            "subagent.start",
            {"task_id": "sa-1", "agent_name": "Explorer-1"},
        )
        await plugin_mgr.publish(
            "subagent.start",
            {"task_id": "sa-2", "agent_name": "Explorer-2"},
        )
        await pilot.pause()
        assert set(panel.active_task_ids()) == {"sa-1", "sa-2"}
        assert panel.has_class("-busy")


@pytest.mark.asyncio
async def test_panel_reaps_after_fade_and_hides():
    """Completed rows fade out and the panel returns to display: none."""
    from aru.plugins.manager import PluginManager
    from aru.tui.app import AruApp
    from aru.tui.widgets.subagent_panel import SubagentPanel

    plugin_mgr = PluginManager()
    plugin_mgr._loaded = True
    app = AruApp(plugin_manager=plugin_mgr)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(SubagentPanel)
        await plugin_mgr.publish(
            "subagent.start",
            {"task_id": "sa-1", "agent_name": "Explorer-1"},
        )
        await plugin_mgr.publish(
            "subagent.complete",
            {"task_id": "sa-1", "status": "completed"},
        )
        await pilot.pause()
        assert "sa-1" in panel._rows

        # Force the row's done_at into the past so the next tick reaps
        # it without us needing to actually sleep ~3 seconds.
        panel._rows["sa-1"]["done_at"] = time.monotonic() - panel.FADE_SECONDS - 1
        panel._tick()
        await pilot.pause()
        assert panel.active_task_ids() == []
        assert not panel.has_class("-busy")


@pytest.mark.asyncio
async def test_panel_ignores_unknown_task_ids():
    """tool.started for an unseen task_id must not raise or auto-create rows."""
    from aru.plugins.manager import PluginManager
    from aru.tui.app import AruApp
    from aru.tui.widgets.subagent_panel import SubagentPanel

    plugin_mgr = PluginManager()
    plugin_mgr._loaded = True
    app = AruApp(plugin_manager=plugin_mgr)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(SubagentPanel)
        await plugin_mgr.publish(
            "subagent.tool.started",
            {"task_id": "ghost", "tool_name": "grep_search"},
        )
        await plugin_mgr.publish(
            "subagent.complete",
            {"task_id": "ghost", "status": "completed"},
        )
        await pilot.pause()
        # No row was ever started — the panel must stay silent.
        assert panel.active_task_ids() == []
        assert not panel.has_class("-busy")


@pytest.mark.asyncio
async def test_panel_dedupes_duplicate_starts():
    """Two starts with the same task_id keep one row (resume case)."""
    from aru.plugins.manager import PluginManager
    from aru.tui.app import AruApp
    from aru.tui.widgets.subagent_panel import SubagentPanel

    plugin_mgr = PluginManager()
    plugin_mgr._loaded = True
    app = AruApp(plugin_manager=plugin_mgr)
    async with app.run_test() as pilot:
        await pilot.pause()
        panel = app.query_one(SubagentPanel)
        for _ in range(2):
            await plugin_mgr.publish(
                "subagent.start",
                {"task_id": "sa-1", "agent_name": "Explorer-1"},
            )
        await pilot.pause()
        assert panel.active_task_ids() == ["sa-1"]
