"""Tests for the TUI shell boot (E2)."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_app_mounts_and_exits_cleanly():
    """The minimal AruApp should start, be interactive, and exit via action."""
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatPane

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Verify chat pane is composed (E3b — replaced the placeholder).
        assert app.query_one(ChatPane) is not None
        # Trigger quit action — should exit cleanly.
        app.action_quit_app()
        await pilot.pause()
    assert app.return_code == 0


@pytest.mark.asyncio
async def test_ctrl_q_exits():
    """Ctrl+Q binding triggers the quit action."""
    from aru.tui.app import AruApp

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
    assert app.return_code == 0


@pytest.mark.asyncio
async def test_tui_ui_notify_safe():
    """TuiUI.notify / print should not raise even with a running app."""
    from aru.tui.app import AruApp
    from aru.tui.ui import TuiUI

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        ui = TuiUI(app)
        ui.notify("hello")
        ui.print("info")
        app.action_quit_app()
        await pilot.pause()


def test_app_bindings_contain_quit():
    """Sanity: binding registry has a Ctrl+Q entry for quit."""
    from aru.tui.app import AruApp

    keys = {
        b.key for b in AruApp.BINDINGS if hasattr(b, "key")
    }
    assert "ctrl+q" in keys


def test_runtime_ctx_has_tui_slots():
    """RuntimeContext must expose tui_app and ui fields (E2)."""
    from aru.runtime import RuntimeContext

    ctx = RuntimeContext()
    assert ctx.tui_app is None
    assert ctx.ui is None
    # Assignable
    ctx.tui_app = object()
    ctx.ui = object()
    assert ctx.tui_app is not None
    assert ctx.ui is not None


def test_main_wires_tui_flag(monkeypatch):
    """main() should route `--tui` to run_tui without invoking run_cli.

    We stub asyncio.run so main() can observe the call synchronously
    (main is a sync function; we don't want pytest's event loop here).
    """
    import asyncio as _asyncio
    import sys

    called = {"run_tui": 0, "run_cli": 0, "run_oneshot": 0}

    async def fake_run_tui(**kwargs):
        called["run_tui"] += 1

    async def fake_run_cli(**kwargs):
        called["run_cli"] += 1

    async def fake_run_oneshot(*args, **kwargs):
        called["run_oneshot"] += 1

    def fake_asyncio_run(coro):
        # Inspect which coroutine we were handed.
        name = getattr(coro, "__qualname__", "") or getattr(coro, "cr_code", object()).__repr__()
        if "run_tui" in name:
            called["run_tui"] += 1
        elif "run_cli" in name:
            called["run_cli"] += 1
        elif "run_oneshot" in name:
            called["run_oneshot"] += 1
        # Consume the coro so no RuntimeWarning leaks.
        coro.close()

    from aru import cli as cli_mod

    monkeypatch.setattr(cli_mod, "run_cli", fake_run_cli)
    monkeypatch.setattr(cli_mod, "run_oneshot", fake_run_oneshot)
    monkeypatch.setattr("aru.tui.run_tui", fake_run_tui)
    monkeypatch.setattr(cli_mod.asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "argv", ["aru", "--tui"])

    cli_mod.main()

    assert called["run_tui"] == 1
    assert called["run_cli"] == 0
    assert called["run_oneshot"] == 0


def test_main_without_tui_flag_routes_to_repl(monkeypatch):
    """No --tui means main() uses run_cli (REPL) as before."""
    import sys

    called = {"run_tui": 0, "run_cli": 0}

    def fake_asyncio_run(coro):
        name = getattr(coro, "__qualname__", "")
        if "run_tui" in name:
            called["run_tui"] += 1
        elif "run_cli" in name:
            called["run_cli"] += 1
        coro.close()

    from aru import cli as cli_mod
    monkeypatch.setattr(cli_mod.asyncio, "run", fake_asyncio_run)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "argv", ["aru"])

    cli_mod.main()

    assert called["run_tui"] == 0
    assert called["run_cli"] == 1
