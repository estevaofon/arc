"""Tests for the arrow-key option selector.

Uses prompt_toolkit's `create_pipe_input` + `DummyOutput` to simulate
keyboard events without touching real stdin/stdout. That lets us test the
key bindings deterministically in CI where there's no TTY.

Scenarios covered:
1. Non-TTY fallback returns the default index immediately.
2. Arrow keys move the cursor (down, up, wrap-around).
3. Enter confirms the current index.
4. Number-key shortcuts select + confirm in one keystroke.
5. Ctrl+C / Esc return the cancel_value.
6. Empty options list raises ValueError.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aru.select import select_option


# ── Non-TTY fallback ─────────────────────────────────────────────────

class TestNonInteractive:
    def test_no_tty_returns_default(self):
        with patch("sys.stdin.isatty", return_value=False):
            result = select_option(["A", "B", "C"], default=1)
        assert result == 1

    def test_no_tty_returns_default_zero(self):
        with patch("sys.stdin.isatty", return_value=False):
            result = select_option(["A", "B"], default=0)
        assert result == 0

    def test_no_tty_clamps_default_to_range(self):
        """Out-of-range default should be clamped rather than crashing."""
        with patch("sys.stdin.isatty", return_value=False):
            result = select_option(["A", "B"], default=99)
        assert result == 1

    def test_empty_options_raises(self):
        with pytest.raises(ValueError):
            select_option([])


# ── Interactive key handling ─────────────────────────────────────────

def _run_with_keys(keys: str, options: list[str], **kwargs) -> int | None:
    """Run select_option inside a pipe-input session with pre-fed keys.

    Keys can include escape sequences for arrows (e.g. '\x1b[B' = down).
    """
    with patch("sys.stdin.isatty", return_value=True):
        with create_pipe_input() as pipe_input:
            with create_app_session(input=pipe_input, output=DummyOutput()):
                pipe_input.send_text(keys)
                return select_option(options, **kwargs)


class TestArrowNavigation:
    def test_enter_without_movement_returns_default(self):
        # Just Enter — should return the default index.
        result = _run_with_keys("\r", ["Yes", "No"], default=0)
        assert result == 0

    def test_down_arrow_moves_cursor(self):
        # Down, Enter → index 1
        result = _run_with_keys("\x1b[B\r", ["Yes", "No"], default=0)
        assert result == 1

    def test_two_down_arrows(self):
        # Down, Down, Enter → index 2
        result = _run_with_keys("\x1b[B\x1b[B\r", ["Yes", "Auto", "No"], default=0)
        assert result == 2

    def test_up_arrow_from_default_wraps(self):
        # Up from index 0 should wrap to last.
        result = _run_with_keys("\x1b[A\r", ["Yes", "No"], default=0)
        assert result == 1

    def test_down_past_end_wraps(self):
        # Down from index 1 (last) should wrap to 0.
        result = _run_with_keys("\x1b[B\r", ["Yes", "No"], default=1)
        assert result == 0

    def test_up_after_down_cancels_out(self):
        # Down then Up returns to the default.
        result = _run_with_keys("\x1b[B\x1b[A\r", ["A", "B", "C"], default=0)
        assert result == 0

    def test_vim_j_k_navigation(self):
        # j (down), k (up) — same as arrows.
        result = _run_with_keys("j\r", ["A", "B"], default=0)
        assert result == 1

    def test_ctrl_n_ctrl_p_navigation(self):
        # Emacs-style: Ctrl+N down, Ctrl+P up.
        result = _run_with_keys("\x0e\r", ["A", "B"], default=0)
        assert result == 1


class TestNumberShortcuts:
    def test_number_key_selects_and_confirms(self):
        # Pressing "2" should select index 1 immediately.
        result = _run_with_keys("2", ["Yes", "No", "Auto"], default=0)
        assert result == 1

    def test_number_key_first(self):
        result = _run_with_keys("1", ["Yes", "No"], default=1)
        assert result == 0

    def test_number_key_third(self):
        result = _run_with_keys("3", ["Yes", "Auto", "No"], default=0)
        assert result == 2


class TestCancel:
    def test_ctrl_c_returns_cancel_value(self):
        # Ctrl+C → cancel_value (default None)
        result = _run_with_keys("\x03", ["A", "B"], default=0, cancel_value=99)
        assert result == 99

    def test_ctrl_c_default_cancel_is_none(self):
        result = _run_with_keys("\x03", ["A", "B"], default=0)
        assert result is None

    def test_escape_returns_cancel_value(self):
        result = _run_with_keys("\x1b", ["A", "B"], default=0, cancel_value=42)
        assert result == 42


# ── Async-context regression ─────────────────────────────────────────

class TestAsyncContext:
    """Regression for the exit_plan_mode bug report:

        RuntimeError: asyncio.run() cannot be called from a running
        event loop

    `prompt_toolkit.Application.run()` calls `asyncio.run()` internally,
    which fails when the caller is already inside a coroutine (as happens
    when Aru's async tool wrappers invoke `select_option` from
    `exit_plan_mode` or a permission check). `_run_app_sync` must detect
    the running loop and offload `app.run()` to a worker thread.
    """

    @pytest.mark.asyncio
    async def test_run_app_sync_works_inside_running_loop(self):
        from aru.select import _run_app_sync

        # Fake Application that reproduces the failure mode exactly:
        # its .run() method calls asyncio.run() internally, just like
        # the real prompt_toolkit Application does.
        class _FakeApp:
            def __init__(self):
                self.was_called = False

            def run(self):
                import asyncio as _a

                async def _coro():
                    return 42

                self.was_called = True
                return _a.run(_coro())

        fake = _FakeApp()
        # Inside this test coroutine there is a running loop. If the fix
        # is absent, _run_app_sync → app.run() → asyncio.run() raises
        # "asyncio.run() cannot be called from a running event loop".
        result = _run_app_sync(fake)

        assert result == 42
        assert fake.was_called

    def test_run_app_sync_uses_direct_call_when_no_loop(self):
        """Sync context uses the fast path (no thread offload)."""
        from aru.select import _run_app_sync

        class _FakeApp:
            def __init__(self):
                self.thread_id = None

            def run(self):
                import threading
                self.thread_id = threading.get_ident()
                return "ok"

        import threading
        main_thread = threading.get_ident()

        fake = _FakeApp()
        result = _run_app_sync(fake)

        assert result == "ok"
        # Fast path: ran on the same thread that called us.
        assert fake.thread_id == main_thread
