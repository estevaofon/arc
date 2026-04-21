"""Ctrl+B toggles sidebar visibility + assistant messages scroll in place."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_toggle_sidebar_hides_and_restores():
    from aru.tui.app import AruApp

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        sidebar = app.query_one("#sidebar")
        assert not sidebar.has_class("-hidden")
        app.action_toggle_sidebar()
        await pilot.pause()
        assert sidebar.has_class("-hidden")
        app.action_toggle_sidebar()
        await pilot.pause()
        assert not sidebar.has_class("-hidden")


@pytest.mark.asyncio
async def test_assistant_message_flows_into_chat_scroll():
    """Long assistant replies expand the widget; ChatPane owns the scroll.

    OpenCode-parity: we removed the per-bubble ``max-height`` cap so big
    code dumps don't get trapped in a nested scroll box. The widget is
    free to grow and the outer VerticalScroll (ChatPane) handles it.
    """
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatMessageWidget, ChatPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        chat.start_assistant_message()
        big = "\n".join(f"line {i}" for i in range(200))
        chat.append_assistant_delta(big)
        await pilot.pause(0.15)
        chat.finalize_assistant_message()
        await pilot.pause()
        assistants = [
            m for m in chat.query(ChatMessageWidget) if m.role == "assistant"
        ]
        final = assistants[-1]
        max_height = final.styles.max_height
    assert assistants
    assert len(final.buffer) > 1000
    # No inner cap — either unset or "auto". Any concrete cell value
    # would bring back the nested-scrollbar bug.
    assert max_height is None or str(max_height) == "auto"


@pytest.mark.asyncio
async def test_chat_pane_width_expands_when_sidebar_hidden():
    """Chat width ratio increases after ``toggle_sidebar``."""
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatPane

    app = AruApp()
    async with app.run_test(size=(140, 30)) as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        initial_width = chat.size.width
        app.action_toggle_sidebar()
        await pilot.pause()
        expanded_width = chat.size.width
    assert expanded_width > initial_width
