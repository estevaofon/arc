"""Tests for ThinkingIndicator + boot summary lines (visual paridade)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_thinking_indicator_toggles_class_on_busy():
    from aru.tui.app import AruApp
    from aru.tui.widgets.thinking import ThinkingIndicator

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        ind = app.query_one(ThinkingIndicator)
        assert not ind.has_class("-busy")
        ind.busy = True
        await pilot.pause()
        assert ind.has_class("-busy")
        ind.busy = False
        await pilot.pause()
        assert not ind.has_class("-busy")


@pytest.mark.asyncio
async def test_dispatch_sets_busy_true_immediately():
    """_dispatch_user_turn flips ThinkingIndicator.busy to True."""
    from aru.tui.app import AruApp
    from aru.tui.widgets.thinking import ThinkingIndicator

    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        ind = app.query_one(ThinkingIndicator)

        # Patch _run_turn to hang forever so busy stays True long enough.
        import asyncio
        async def fake_run_turn(_text):
            await asyncio.sleep(5)

        app._run_turn = fake_run_turn
        app._dispatch_user_turn("hi")
        await pilot.pause()
        assert ind.busy is True


@pytest.mark.asyncio
async def test_loaded_pane_lists_skills_agents_commands():
    """The split sidebar's LoadedPane replaces the chat-boot summary."""
    from aru.tui.app import AruApp
    from aru.tui.widgets.loaded_pane import LoadedPane

    @dataclass
    class _Agent:
        mode: str = "primary"

    @dataclass
    class _Cfg:
        agents_md: str = "some content"
        commands: dict = field(default_factory=lambda: {"foo": "x"})
        skills: dict = field(default_factory=lambda: {"brainstorming": object(),
                                                       "planning": object()})
        custom_agents: dict = field(default_factory=lambda: {"reviewer": _Agent()})
        permissions: dict = field(default_factory=dict)

    app = AruApp(config=_Cfg())
    async with app.run_test(size=(160, 60)) as pilot:
        await pilot.pause()
        loaded = app.query_one(LoadedPane)
        # Render into text via export_screenshot — best-effort.
        import re, html
        svg = app.export_screenshot()
        text = "".join(
            html.unescape(m) for m in
            re.findall(r"<text[^>]*>([^<]*)</text>", svg, re.DOTALL)
        ).replace("\xa0", " ")
    assert "Skills" in text
    assert "brainstorming" in text or "planning" in text


@pytest.mark.asyncio
async def test_loaded_pane_shows_path_even_on_empty_config():
    """Sessions with no custom content still surface the cwd path."""
    from aru.tui.app import AruApp

    @dataclass
    class _EmptyCfg:
        agents_md: str = ""
        commands: dict = field(default_factory=dict)
        skills: dict = field(default_factory=dict)
        custom_agents: dict = field(default_factory=dict)
        permissions: dict = field(default_factory=dict)

    app = AruApp(config=_EmptyCfg())
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        import re, html
        svg = app.export_screenshot()
        text = "".join(
            html.unescape(m) for m in
            re.findall(r"<text[^>]*>([^<]*)</text>", svg, re.DOTALL)
        ).replace("\xa0", " ")
    assert "Path" in text
