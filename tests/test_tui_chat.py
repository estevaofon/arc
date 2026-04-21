"""Tests for ChatPane + TextualBusSink (E3b)."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_chat_pane_user_message():
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatMessageWidget, ChatPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        chat.add_user_message("hello")
        await pilot.pause()
        # One system (startup banner) + one user message at minimum
        msgs = list(chat.query(ChatMessageWidget))
        # The last should be the user message.
        user_msgs = [m for m in msgs if m.role == "user"]
        assert user_msgs and user_msgs[-1].buffer == "hello"


@pytest.mark.asyncio
async def test_chat_pane_assistant_streaming():
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatMessageWidget, ChatPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        chat.start_assistant_message()
        chat.append_assistant_delta("Hel")
        chat.append_assistant_delta("lo!")
        # Wait for debounce flush
        await pilot.pause(0.15)
        chat.finalize_assistant_message()
        await pilot.pause()
        assistants = [
            m for m in chat.query(ChatMessageWidget) if m.role == "assistant"
        ]
        assert assistants
        assert assistants[-1].buffer == "Hello!"


@pytest.mark.asyncio
async def test_chat_pane_tool_lifecycle():
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatMessageWidget, ChatPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        chat.add_tool_call(tool_id="t1", label="Read(file.py)")
        await pilot.pause()
        # Pending widget exists with pending state
        tool_msgs = [m for m in chat.query(ChatMessageWidget) if m.role == "tool"]
        assert any("Read" in m.buffer for m in tool_msgs)
        pending = [m for m in tool_msgs if m.tool_state == "pending"]
        assert len(pending) == 1

        chat.complete_tool_call(tool_id="t1", label="Read(file.py)", duration_ms=100)
        await pilot.pause()
        tool_msgs = [m for m in chat.query(ChatMessageWidget) if m.role == "tool"]
        done = [m for m in tool_msgs if m.tool_state == "done"]
        assert len(done) == 1


@pytest.mark.asyncio
async def test_textual_bus_sink_implements_protocol():
    from aru.streaming import StreamSink
    from aru.tui.app import AruApp
    from aru.tui.sinks import TextualBusSink
    from aru.tui.widgets.chat import ChatPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        sink = TextualBusSink(app=app, chat_pane=chat)
        assert isinstance(sink, StreamSink)


@pytest.mark.asyncio
async def test_textual_bus_sink_forwards_content_delta():
    from aru.tui.app import AruApp
    from aru.tui.sinks import TextualBusSink
    from aru.tui.widgets.chat import ChatMessageWidget, ChatPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        sink = TextualBusSink(app=app, chat_pane=chat)
        sink.enter()  # opens assistant bubble
        await pilot.pause()
        sink.on_content_delta(delta="abc", accumulated="abc")
        await pilot.pause(0.15)  # wait for debounce flush
        assistants = [
            m for m in chat.query(ChatMessageWidget) if m.role == "assistant"
        ]
        assert assistants and "abc" in assistants[-1].buffer
        sink.exit()
        await pilot.pause()
