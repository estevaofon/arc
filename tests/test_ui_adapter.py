"""Tests for the UIAdapter protocol + ReplUI (E6a)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aru.ui import ReplUI, UIAdapter


def test_repl_ui_satisfies_protocol():
    assert isinstance(ReplUI(), UIAdapter)


def test_repl_ui_ask_choice_delegates_to_select_option():
    ui = ReplUI()
    with patch("aru.select.select_option", return_value=1) as mock_select:
        result = ui.ask_choice(
            ["A", "B"], title="pick one", default=0, cancel_value=None
        )
    assert result == 1
    mock_select.assert_called_once()
    kwargs = mock_select.call_args.kwargs
    assert kwargs["title"] == "pick one"
    assert kwargs["default"] == 0


def test_repl_ui_confirm_delegates_to_ask_yes_no():
    ui = ReplUI()
    with patch("aru.commands.ask_yes_no", return_value=True) as mock_ask:
        result = ui.confirm("really?", default=False)
    assert result is True
    mock_ask.assert_called_once_with("really?")


def test_repl_ui_notify_writes_to_console():
    from rich.console import Console

    console = Console(record=True)
    ui = ReplUI(console=console)
    ui.notify("warning text", severity="warn")
    out = console.export_text()
    assert "warning text" in out


def test_install_repl_ui_is_idempotent():
    from aru.ui import install_repl_ui_on_ctx

    class FakeCtx:
        ui = None

    ctx = FakeCtx()
    ui1 = install_repl_ui_on_ctx(ctx)
    ui2 = install_repl_ui_on_ctx(ctx)
    assert ui1 is ui2
    assert isinstance(ctx.ui, ReplUI)
