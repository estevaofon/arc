"""Tests for TUI modal screens (E7 — infra)."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_choice_modal_returns_selected_index():
    from aru.tui.app import AruApp
    from aru.tui.screens import ChoiceModal

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        result_holder: list = []

        def _store(value):
            result_holder.append(value)

        app.push_screen(ChoiceModal(["Allow", "Deny"], title="Test"), _store)
        await pilot.pause()
        # Default cursor is 0 — select via Enter.
        await pilot.press("enter")
        await pilot.pause()
    assert result_holder == [0]


@pytest.mark.asyncio
async def test_choice_modal_escape_returns_cancel_value():
    from aru.tui.app import AruApp
    from aru.tui.screens import ChoiceModal

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        result_holder: list = []

        def _store(value):
            result_holder.append(value)

        app.push_screen(
            ChoiceModal(["A", "B"], cancel_value=-1), _store
        )
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result_holder == [-1]


@pytest.mark.asyncio
async def test_confirm_modal_yes_key():
    from aru.tui.app import AruApp
    from aru.tui.screens import ConfirmModal

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        result_holder: list = []

        def _store(value):
            result_holder.append(value)

        app.push_screen(ConfirmModal("Confirm?"), _store)
        await pilot.pause()
        await pilot.press("y")
        await pilot.pause()
    assert result_holder == [True]


@pytest.mark.asyncio
async def test_text_input_modal_returns_value():
    from aru.tui.app import AruApp
    from aru.tui.screens import TextInputModal

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        result_holder: list = []

        def _store(value):
            result_holder.append(value)

        app.push_screen(TextInputModal("Enter text:"), _store)
        await pilot.pause()
        # Type a value and submit
        for ch in "hello":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
    assert result_holder == ["hello"]


@pytest.mark.asyncio
async def test_text_input_modal_escape_returns_none():
    from aru.tui.app import AruApp
    from aru.tui.screens import TextInputModal

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        result_holder: list = []

        def _store(value):
            result_holder.append(value)

        app.push_screen(TextInputModal("Enter:"), _store)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result_holder == [None]
