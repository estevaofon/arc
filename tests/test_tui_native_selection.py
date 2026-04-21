"""Native text-selection over ChatMessageWidget (Static subclass)."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_chat_message_widget_is_static_subclass():
    """ChatMessageWidget must subclass Static so selection traversal works."""
    from textual.widgets import Static
    from aru.tui.widgets.chat import ChatMessageWidget
    assert issubclass(ChatMessageWidget, Static)


@pytest.mark.asyncio
async def test_chat_message_widget_allows_select():
    """ALLOW_SELECT stays True — selection machinery visits this widget."""
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatMessageWidget, ChatPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        chat.start_assistant_message()
        chat.append_assistant_delta("some reply")
        await pilot.pause(0.15)
        chat.finalize_assistant_message()
        assistants = [
            m for m in chat.query(ChatMessageWidget) if m.role == "assistant"
        ]
    assert assistants
    for msg in assistants:
        # Each message consent to being selected (class + instance level).
        assert msg.ALLOW_SELECT is True
        assert getattr(msg, "allow_select", True) is True


@pytest.mark.asyncio
async def test_chat_pane_selection_api_available():
    """Screen.get_selected_text + clear_selection are callable — the
    plumbing Textual exposes for Ctrl+C selection copy."""
    from aru.tui.app import AruApp

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert callable(app.screen.get_selected_text)
        assert callable(app.screen.clear_selection)
        # No selection yet → None or empty string.
        assert (app.screen.get_selected_text() or "") == ""
