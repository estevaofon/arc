"""Tests for the minimal input behaviour added in E6c."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_user_message_persists_to_session_history():
    """Plain-text messages must land in ``session.history`` as user turns.

    Regression guard: before fix/tui-freezing2, ``_dispatch_user_turn``
    did not call ``session.add_message("user", ...)`` (the REPL did, but
    the TUI forwarded straight to ``run_agent_capture_tui``). Session
    files wrote back only ``assistant`` + ``tool`` turns, so a reloaded
    session had no user context and follow-up prompts like ``continue``
    left the agent thinking for a tick and halting — no user side to
    reason against.
    """
    from aru.tui.app import AruApp
    from aru.session import Session

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.session = Session(session_id="test-persist")
        assert app.session.history == []

        app._dispatch_user_turn("hello world")
        # Persistence happens synchronously, before the worker dispatch,
        # so we can check immediately — no need to await the worker
        # (which would fail on an uninstalled runtime anyway).

        user_msgs = [m for m in app.session.history if m.get("role") == "user"]
        assert len(user_msgs) == 1
        blocks = user_msgs[0]["content"]
        text = "".join(
            b.get("text", "")
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )
        assert text == "hello world"


@pytest.mark.asyncio
async def test_multiple_user_turns_accumulate_in_history():
    """Successive ``_dispatch_user_turn`` calls all append — no overwrite."""
    from aru.tui.app import AruApp
    from aru.session import Session

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.session = Session(session_id="test-persist-multi")

        # Simulate three user turns in quick succession. The agent worker
        # is exclusive/grouped, so these enqueue rather than actually
        # running — which is exactly what we need to isolate the
        # persistence path.
        for msg in ("first", "second", "continue"):
            app._dispatch_user_turn(msg)

        user_msgs = [m for m in app.session.history if m.get("role") == "user"]
        assert len(user_msgs) == 3
        plain_texts = []
        for m in user_msgs:
            plain_texts.append(
                "".join(
                    b.get("text", "")
                    for b in m["content"]
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            )
        assert plain_texts == ["first", "second", "continue"]


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
async def test_multiline_paste_preserves_full_block():
    """Pasting N>1 lines stashes the full block; submit sends everything.

    Regression: Textual's base ``Input._on_paste`` silently drops
    everything after the first newline. For agent prompts the whole
    block must survive so stack traces / diffs / log snippets reach
    the agent intact.
    """
    from textual import events
    from textual.widgets import Input

    from aru.tui.app import AruApp, PromptInput
    from aru.tui.widgets.chat import ChatMessageWidget, ChatPane

    captured: dict = {}

    class _Probe(AruApp):
        def _dispatch_user_turn(self, text: str) -> None:  # type: ignore[override]
            captured["text"] = text
            # Skip the real dispatch (no agent available in tests).
            self.query_one(ChatPane).add_user_message(text)

    app = _Probe()
    pasted = "line one\nline two\nline three"
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one(Input)
        assert isinstance(inp, PromptInput), "compose must yield PromptInput"
        inp.post_message(events.Paste(text=pasted))
        await pilot.pause()
        # The full text is stashed, the visible input stays empty.
        assert app._pending_paste == pasted
        assert app._pending_paste_lines == 3
        assert inp.value == ""
        # Submit with no annotation → agent sees the raw pasted block.
        inp.post_message(Input.Submitted(inp, value=""))
        await pilot.pause()
    assert captured["text"] == pasted
    assert app._pending_paste is None


@pytest.mark.asyncio
async def test_multiline_paste_with_annotation_wraps_in_fenced_block():
    """If the user types a note and submits, the paste is merged as fenced code."""
    from textual import events
    from textual.widgets import Input

    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatPane

    captured: dict = {}

    class _Probe(AruApp):
        def _dispatch_user_turn(self, text: str) -> None:  # type: ignore[override]
            captured["text"] = text
            self.query_one(ChatPane).add_user_message(text)

    app = _Probe()
    pasted = "err: foo\nerr: bar"
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one(Input)
        inp.post_message(events.Paste(text=pasted))
        await pilot.pause()
        inp.post_message(Input.Submitted(inp, value="what does this mean?"))
        await pilot.pause()
    expected = f"what does this mean?\n\n```\n{pasted}\n```"
    assert captured["text"] == expected


@pytest.mark.asyncio
async def test_single_line_paste_falls_back_to_default_behaviour():
    """A single-line paste must NOT land in ``_pending_paste`` — inserted inline."""
    from textual import events
    from textual.widgets import Input

    from aru.tui.app import AruApp

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query_one(Input)
        inp.post_message(events.Paste(text="just one line"))
        await pilot.pause()
        assert app._pending_paste is None
        assert app._pending_paste_lines == 0
        assert inp.value == "just one line"


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
