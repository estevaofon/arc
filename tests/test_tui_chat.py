"""Tests for ChatPane + TextualBusSink (E3b)."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


# ── _find_last_stable_split — pure-function unit tests ───────────────
# These don't need the Textual app machinery; they exercise the boundary
# scanner used by the incremental markdown-render path.


def test_split_plain_paragraphs_returns_after_last_blank():
    from aru.tui.widgets.chat import _find_last_stable_split

    text = "para1\n\npara2\n\npara3 in progress"
    idx = _find_last_stable_split(text)
    # text[:idx] must end on the last blank line's terminator
    assert idx > 0
    assert text[:idx].endswith("\n\n")
    # And the tail is the still-growing final paragraph
    assert text[idx:] == "para3 in progress"


def test_split_blank_line_inside_open_fence_is_ignored():
    from aru.tui.widgets.chat import _find_last_stable_split

    # The blank line sits inside a still-open fence — no valid split.
    text = "intro\n\n```python\n\nstill inside fence\n"
    idx = _find_last_stable_split(text)
    # The only stable blank is the one after "intro"
    assert idx == len("intro\n\n")
    # Confirm the fence content is the tail
    assert text[idx:].startswith("```python")


def test_split_single_giant_unclosed_fence_returns_minus_one():
    from aru.tui.widgets.chat import _find_last_stable_split

    text = "```python\n" + "x = 1\n" * 500 + "\n" * 3
    # Fence is open; every blank is inside it
    assert _find_last_stable_split(text) == -1


def test_split_closed_fence_followed_by_blank_returns_post_blank():
    from aru.tui.widgets.chat import _find_last_stable_split

    text = "```python\nx = 1\n```\n\nmore prose"
    idx = _find_last_stable_split(text)
    assert idx == len("```python\nx = 1\n```\n\n")
    assert text[idx:] == "more prose"


def test_split_tilde_fence_variant():
    from aru.tui.widgets.chat import _find_last_stable_split

    text = "~~~\nfoo\n~~~\n\nbar"
    idx = _find_last_stable_split(text)
    assert idx == len("~~~\nfoo\n~~~\n\n")
    assert text[idx:] == "bar"


def test_split_crlf_line_endings():
    from aru.tui.widgets.chat import _find_last_stable_split

    text = "para1\r\n\r\npara2\r\n\r\ntail"
    idx = _find_last_stable_split(text)
    # Offset is byte-exact; text[:idx] ends with the second \r\n\r\n
    assert text[:idx].endswith("\r\n\r\n")
    assert text[idx:] == "tail"


def test_split_indented_code_block_does_not_open_fence():
    from aru.tui.widgets.chat import _find_last_stable_split

    # Four leading spaces → indented code block per CommonMark. The ```
    # on that line is literal, not a fence opener, so the blank line
    # after the "real" paragraph is still a valid split.
    text = "para\n\n    ```python\n    still literal\n\ntail"
    idx = _find_last_stable_split(text)
    # Either of the two blank lines is outside a fence; the last one wins.
    assert text[:idx].endswith("\n\n")
    assert text[idx:] == "tail"


def test_split_empty_and_trivial():
    from aru.tui.widgets.chat import _find_last_stable_split

    assert _find_last_stable_split("") == -1
    assert _find_last_stable_split("just one paragraph") == -1
    # A single trailing newline isn't a "blank line" — no split.
    assert _find_last_stable_split("one para\n") == -1


# ── _scan_fences — dual-boundary scanner used by the escape hatch ────


def test_scan_fences_no_fences():
    from aru.tui.widgets.chat import _scan_fences

    split, fence = _scan_fences("just prose\n\nmore prose\n\nfinal")
    assert split > 0
    assert fence == -1


def test_scan_fences_closed_fence_reports_no_open():
    from aru.tui.widgets.chat import _scan_fences

    text = "para\n\n```python\nx = 1\n```\n\ntail"
    split, fence = _scan_fences(text)
    assert split > 0  # last blank line
    assert fence == -1  # fence is closed


def test_scan_fences_open_fence_reports_start_offset():
    from aru.tui.widgets.chat import _scan_fences

    # Fence opens at offset 6 (after "para\n\n") and never closes.
    text = "para\n\n```python\ndef foo():\n    pass\n"
    split, fence = _scan_fences(text)
    assert split == len("para\n\n")  # stable split still exists
    assert fence == len("para\n\n")  # fence opener line starts right after


def test_scan_fences_whole_buffer_inside_open_fence():
    from aru.tui.widgets.chat import _scan_fences

    text = "```python\n" + "x = 1\n" * 50
    split, fence = _scan_fences(text)
    assert split == -1  # every blank is inside the fence
    assert fence == 0  # opener is the first line


def test_scan_fences_tilde_variant():
    from aru.tui.widgets.chat import _scan_fences

    text = "~~~\nfoo\nbar\n"
    split, fence = _scan_fences(text)
    assert fence == 0  # opens at start, never closes


def test_scan_fences_closer_must_match_marker():
    from aru.tui.widgets.chat import _scan_fences

    # Mismatched closer (~~~ doesn't close ```) → fence stays open.
    text = "```\nfoo\n~~~\n"
    split, fence = _scan_fences(text)
    assert fence == 0


# ── Control-character sanitization ──────────────────────────────────
# Rogue ANSI escapes in streamed model output can reach the terminal
# verbatim through Rich's Text and disable mouse tracking globally —
# the "only scroll froze" bug. These tests codify the defence.


def test_sanitize_strips_c0_controls_but_keeps_newline_and_tab():
    from aru.tui.widgets.chat import _sanitize_for_terminal

    raw = "a\x00b\x07c\x1b[?1000ld\te\nf\x7fg"
    assert _sanitize_for_terminal(raw) == "abc[?1000ld\te\nfg"


def test_markdown_to_text_strips_mouse_disable_escape():
    import io
    from rich.console import Console
    from aru.tui.widgets.chat import _markdown_to_text

    evil = "Hello \x1b[?1000l world \x1b[?25l more"
    rendered = _markdown_to_text(evil, 80)
    out = io.StringIO()
    Console(
        file=out, force_terminal=True, legacy_windows=False,
        color_system="truecolor",
    ).print(rendered)
    stream = out.getvalue()
    # Neither the full mouse-tracking-disable escape nor the cursor-hide
    # escape can survive to the terminal.
    assert "\x1b[?1000l" not in stream
    assert "\x1b[?25l" not in stream
    # The raw ESC byte itself must not leak either (other terminal
    # private-mode codes rely on it).
    assert "\x1b[?" not in stream


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
