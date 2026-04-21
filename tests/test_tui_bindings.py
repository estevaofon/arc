"""Tests for E8 bindings, actions, and SearchScreen."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_action_cycle_mode_advances_permission_mode(monkeypatch):
    from aru.tui.app import AruApp

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Stub cycle_permission_mode so we don't require a full ctx.
        called = {"count": 0}

        def fake_cycle():
            called["count"] += 1
            return "acceptEdits"

        monkeypatch.setattr("aru.permissions.cycle_permission_mode", fake_cycle)
        app.action_cycle_mode()
        await pilot.pause()
    assert called["count"] == 1


@pytest.mark.asyncio
async def test_action_toggle_plan_flips_session_flag():
    from aru.tui.app import AruApp

    class _FakeSession:
        plan_mode = False

    session = _FakeSession()
    app = AruApp(session=session)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_toggle_plan()
        await pilot.pause()
        assert session.plan_mode is True
        app.action_toggle_plan()
        await pilot.pause()
        assert session.plan_mode is False


@pytest.mark.asyncio
async def test_search_screen_filters_messages():
    from aru.tui.screens import SearchScreen
    from aru.tui.app import AruApp

    items = [
        (0, "hello world"),
        (1, "refactor login flow"),
        (2, "HELLO again"),
    ]
    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        result_holder: list = []
        app.push_screen(SearchScreen(items), lambda v: result_holder.append(v))
        await pilot.pause()
        # Type "hello" — matches 2 items (case-insensitive)
        for ch in "hello":
            await pilot.press(ch)
        await pilot.pause()
        screen = app.screen
        assert len(screen._filtered) == 2  # type: ignore[attr-defined]
        # Submit — should pick first filtered item (idx 0 in the original list)
        await pilot.press("enter")
        await pilot.pause()
    assert result_holder == [0]


@pytest.mark.asyncio
async def test_search_screen_escape_returns_none():
    from aru.tui.screens import SearchScreen
    from aru.tui.app import AruApp

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        result_holder: list = []
        app.push_screen(SearchScreen([(0, "foo")]), lambda v: result_holder.append(v))
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result_holder == [None]


@pytest.mark.asyncio
async def test_app_has_expected_bindings():
    from aru.tui.app import AruApp

    keys = {
        b.key: b.action for b in AruApp.BINDINGS if hasattr(b, "key")
    }
    assert "ctrl+q" in keys
    assert "ctrl+l" in keys
    assert "ctrl+a" in keys
    assert "ctrl+p" in keys
    assert "ctrl+f" in keys
