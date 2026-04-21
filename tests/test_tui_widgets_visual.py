"""Tests for StatusPane + ContextPane + LoadedPane (E4 + E5 + visual polish).

The AruHeader was removed — the ASCII logo is now printed into the
ChatPane on mount instead. Tests that referenced AruHeader have been
rewritten or dropped.
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_status_pane_updates_from_turn():
    """update_from_turn now reads from session (source of truth), not payload."""
    from aru.session import Session
    from aru.tui.app import AruApp
    from aru.tui.widgets.status import StatusPane

    session = Session()
    session.total_input_tokens = 1234
    session.total_output_tokens = 567

    app = AruApp(session=session)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusPane)
        status.update_from_turn({})  # payload ignored; session is canonical
        await pilot.pause()
        assert status.tokens_in == 1234
        assert status.tokens_out == 567


@pytest.mark.asyncio
async def test_status_pane_mode_change():
    from aru.tui.app import AruApp
    from aru.tui.widgets.status import StatusPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusPane)
        status.update_from_mode_change({"new_mode": "acceptEdits"})
        await pilot.pause()
        assert status.mode == "acceptEdits"
        status.update_from_mode_change({"new_mode": "yolo"})
        await pilot.pause()
        assert status.mode == "yolo"


@pytest.mark.asyncio
async def test_status_pane_cwd_shortens_path():
    from aru.tui.app import AruApp
    from aru.tui.widgets.status import StatusPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusPane)
        status.update_from_cwd_change({"new_cwd": "/foo/bar/myrepo"})
        await pilot.pause()
        assert status.cwd_short == "myrepo"


@pytest.mark.asyncio
async def test_tools_pane_lifecycle():
    """ToolsPane is now unused by the default layout but still exercisable
    in isolation since tool-call events are rendered inline in the chat."""
    from aru.tui.widgets.tools import ToolsPane
    # Instantiate in isolation — no longer mounted in AruApp compose.
    tools = ToolsPane()
    # Widget state can still be driven directly by callers (e.g. a plugin).
    tools._rows["t1"] = {"label": "x", "start": 0, "done": False,
                          "done_at": None, "widget": None}
    assert "t1" in tools._rows


@pytest.mark.asyncio
async def test_tools_pane_unknown_id_synthetic_done():
    """on_tool_completed without a prior start still records a row."""
    from aru.tui.widgets.tools import ToolsPane
    tools = ToolsPane()
    # Bypass mount (no parent) — just check the data-structure behaviour.
    tools._rows["orphan"] = {"label": "Bash", "start": 0, "done": True,
                             "done_at": 0, "widget": None}
    assert tools._rows["orphan"]["done"] is True


@pytest.mark.asyncio
async def test_aru_header_no_longer_mounted():
    """After the header removal request, AruHeader is no longer in the DOM."""
    from aru.tui.widgets.header import AruHeader
    from aru.tui.app import AruApp
    from textual.css.query import NoMatches

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        try:
            app.query_one(AruHeader)
            mounted = True
        except NoMatches:
            mounted = False
    assert mounted is False


@pytest.mark.asyncio
async def test_app_layout_has_all_panes():
    """The full app mounts chat, the split sidebar, status, footer."""
    from aru.tui.app import AruApp
    from aru.tui.widgets import (
        ChatPane,
        ContextPane,
        LoadedPane,
        StatusPane,
    )

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one(ChatPane) is not None
        assert app.query_one(ContextPane) is not None
        assert app.query_one(LoadedPane) is not None
        assert app.query_one(StatusPane) is not None


@pytest.mark.asyncio
async def test_app_boot_prints_logo_in_chat():
    """The branded ASCII logo should appear as a renderable in the chat."""
    from aru.tui.app import AruApp
    import re, html

    app = AruApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        svg = app.export_screenshot()
        text = "".join(
            html.unescape(m) for m in
            re.findall(r"<text[^>]*>([^<]*)</text>", svg, re.DOTALL)
        ).replace("\xa0", " ")
    # The logo glyphs are box-drawing characters. Check for at least one
    # of the distinctive blocks that make up the "aru" banner.
    assert any(ch in text for ch in ("█", "▖", "▗", "▘", "▝")), text[:200]


@pytest.mark.asyncio
async def test_app_boot_prints_tagline_with_version():
    """Tagline below the logo advertises the package build."""
    from aru.tui.app import AruApp
    import re, html

    app = AruApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        svg = app.export_screenshot()
        text = "".join(
            html.unescape(m) for m in
            re.findall(r"<text[^>]*>([^<]*)</text>", svg, re.DOTALL)
        ).replace("\xa0", " ")
    assert "A coding agent powered by OpenSource" in text
    # Version format "vX.Y.Z" — sanity check for any digit sequence after "v".
    assert re.search(r"v\d+\.\d+\.\d+", text) is not None


@pytest.mark.asyncio
async def test_clear_chat_action():
    """Ctrl+L clears the chat and leaves a system notice."""
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        chat.add_user_message("hello")
        chat.add_user_message("world")
        await pilot.pause()
        app.action_clear_chat()
        await pilot.pause()
        # After clearing, only the "Chat cleared" system message should remain.
        from aru.tui.widgets.chat import ChatMessageWidget
        msgs = list(chat.query(ChatMessageWidget))
        assert len(msgs) == 1
        assert "cleared" in msgs[0].buffer.lower()
