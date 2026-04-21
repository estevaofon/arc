"""Tests for the minimal input behaviour added in E6c."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_slash_help_handled_locally():
    """`/help` prints help inline — does NOT dispatch to the agent."""
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatMessageWidget, ChatPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one("Input")
        inp.value = "/help"
        # Simulate submit
        from textual.widgets import Input
        inp.post_message(Input.Submitted(inp, value="/help"))
        await pilot.pause()
        chat = app.query_one(ChatPane)
        msgs = list(chat.query(ChatMessageWidget))
        joined = " ".join(m.buffer for m in msgs)
        assert "local commands" in joined.lower() or "shortcuts" in joined.lower()
    # App should not be busy (no turn was dispatched)
    assert app._busy is False


@pytest.mark.asyncio
async def test_slash_clear_clears_chat():
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatMessageWidget, ChatPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        chat.add_user_message("one")
        chat.add_user_message("two")
        await pilot.pause()
        inp = app.query_one("Input")
        from textual.widgets import Input as _I
        inp.post_message(_I.Submitted(inp, value="/clear"))
        await pilot.pause()
        msgs = list(chat.query(ChatMessageWidget))
        # Only the "Chat cleared" system message remains.
        assert len(msgs) == 1
        assert "cleared" in msgs[0].buffer.lower()


@pytest.mark.asyncio
async def test_unknown_slash_falls_through_to_agent_queue():
    """A slash command we don't handle locally should NOT be eaten."""
    from aru.tui.app import AruApp

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Directly test _maybe_run_local_slash — returns False = not handled.
        handled = app._maybe_run_local_slash("/mystery")
        assert handled is False


@pytest.mark.asyncio
async def test_history_up_down_cycles_submitted_inputs():
    from aru.tui.app import AruApp
    from textual.widgets import Input

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one(Input)
        # Simulate two submits to populate history (use /clear so no agent runs)
        inp.post_message(Input.Submitted(inp, value="/clear"))
        await pilot.pause()
        inp.post_message(Input.Submitted(inp, value="/help"))
        await pilot.pause()
        # After submits, history has 2 entries; cursor is reset
        assert app._history == ["/clear", "/help"]
        assert app._history_cursor is None
        # Simulate Up → should recall the latest entry
        inp.focus()
        await pilot.pause()
        app.action_history_prev()
        await pilot.pause()
        assert inp.value == "/help"
        app.action_history_prev()
        await pilot.pause()
        assert inp.value == "/clear"
        app.action_history_next()
        await pilot.pause()
        assert inp.value == "/help"
        app.action_history_next()
        await pilot.pause()
        # Past last entry — returns to empty.
        assert inp.value == ""
