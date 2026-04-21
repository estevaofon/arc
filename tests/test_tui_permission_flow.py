"""Integration tests for the TuiUI → ModalScreen flow (E7)."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_tui_ask_choice_from_worker_resolves_via_modal():
    """TuiUI.ask_choice invoked from a worker thread returns modal result.

    Simulates the permission prompt path: tool code (sync) runs in
    asyncio.to_thread, calls ctx.ui.ask_choice(...), modal appears in
    the App, user selects option → choice returned synchronously.
    """
    from aru.tui.app import AruApp
    from aru.tui.ui import TuiUI

    app = AruApp()
    result_holder: dict = {}

    async def worker_calls_ask_choice() -> None:
        ui = TuiUI(app)
        # asyncio.to_thread moves us off the App loop, matching how
        # check_permission is called from tool threads.
        choice = await asyncio.to_thread(
            ui.ask_choice,
            ["Allow", "Deny"],
            title="Test",
            default=0,
            cancel_value=None,
        )
        result_holder["choice"] = choice

    async with app.run_test() as pilot:
        await pilot.pause()
        worker_task = asyncio.create_task(worker_calls_ask_choice())
        # Wait for modal to appear
        for _ in range(50):
            await pilot.pause(0.05)
            from aru.tui.screens import ChoiceModal
            if app.screen_stack and isinstance(app.screen, ChoiceModal):
                break
        # Select option 0 (default highlight) via enter
        await pilot.press("enter")
        await asyncio.wait_for(worker_task, timeout=5.0)
    assert result_holder["choice"] == 0


@pytest.mark.asyncio
async def test_tui_confirm_from_worker_returns_bool():
    from aru.tui.app import AruApp
    from aru.tui.ui import TuiUI

    app = AruApp()
    result_holder: dict = {}

    async def worker_confirm() -> None:
        ui = TuiUI(app)
        answer = await asyncio.to_thread(ui.confirm, "Proceed?", False)
        result_holder["answer"] = answer

    async with app.run_test() as pilot:
        await pilot.pause()
        worker_task = asyncio.create_task(worker_confirm())
        for _ in range(50):
            await pilot.pause(0.05)
            from aru.tui.screens import ConfirmModal
            if app.screen_stack and isinstance(app.screen, ConfirmModal):
                break
        await pilot.press("y")
        await asyncio.wait_for(worker_task, timeout=5.0)
    assert result_holder["answer"] is True
