"""End-to-end: plugin bus → StatusPane/ToolsPane updates (E4/E5 + regression)."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_turn_end_event_updates_status_pane():
    """Publishing turn.end must trigger a session-backed refresh."""
    from aru.plugins.manager import PluginManager
    from aru.session import Session
    from aru.tui.app import AruApp
    from aru.tui.widgets.status import StatusPane

    plugin_mgr = PluginManager()
    plugin_mgr._loaded = True
    session = Session()
    session.total_input_tokens = 4200
    session.total_output_tokens = 888

    app = AruApp(plugin_manager=plugin_mgr, session=session)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusPane)
        # Payload is ignored; StatusPane reads directly from session.
        await plugin_mgr.publish("turn.end", {})
        await pilot.pause()
    assert status.tokens_in == 4200
    assert status.tokens_out == 888


@pytest.mark.asyncio
async def test_tool_called_event_reaches_chat_inline():
    """After the sidebar restructure ToolsPane no longer mounts by default;
    tool lifecycle events are rendered inline in the ChatPane by
    ``TextualBusSink``. We assert the bus delivers the payload so at
    least plugins still observe it."""
    from aru.plugins.manager import PluginManager
    from aru.tui.app import AruApp

    plugin_mgr = PluginManager()
    plugin_mgr._loaded = True
    received: list[dict] = []
    plugin_mgr.subscribe("tool.called", lambda p: received.append(p))

    app = AruApp(plugin_manager=plugin_mgr)
    async with app.run_test() as pilot:
        await pilot.pause()
        await plugin_mgr.publish(
            "tool.called",
            {"tool_id": "t1", "tool_name": "read_file", "label": "Read(x.py)"},
        )
        await pilot.pause()
    assert received and received[0]["tool_id"] == "t1"


@pytest.mark.asyncio
async def test_permission_mode_change_propagates():
    from aru.plugins.manager import PluginManager
    from aru.tui.app import AruApp
    from aru.tui.widgets.status import StatusPane

    plugin_mgr = PluginManager()
    plugin_mgr._loaded = True
    app = AruApp(plugin_manager=plugin_mgr)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusPane)
        await plugin_mgr.publish(
            "permission.mode.changed",
            {"old_mode": "default", "new_mode": "yolo"},
        )
        await pilot.pause()
    assert status.mode == "yolo"
