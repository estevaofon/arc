"""ChatPane supports scrollable blocks for long renderables."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_add_renderable_scrollable_wraps_in_vertical_scroll():
    """Large file contents should be scrollable as their own block."""
    from rich.panel import Panel
    from textual.containers import VerticalScroll

    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatPane

    # A deliberately huge panel — 80 lines.
    body = "\n".join(f"line {i}" for i in range(80))
    panel = Panel(body, title="big-file.py", border_style="green")

    app = AruApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        chat.add_renderable(panel, scrollable=True, max_height=10)
        await pilot.pause()
        # There's a VerticalScroll mounted as a direct child — confirm.
        scrollables = [
            c for c in chat.children if isinstance(c, VerticalScroll)
        ]
    assert scrollables, "expected a VerticalScroll wrapper for the big panel"


@pytest.mark.asyncio
async def test_add_renderable_default_not_scrollable():
    """Without scrollable=True the widget is mounted plain."""
    from textual.containers import VerticalScroll
    from textual.widgets import Static

    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatPane

    app = AruApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        before_scroll = len([
            c for c in chat.children if isinstance(c, VerticalScroll)
        ])
        chat.add_renderable("small text")
        await pilot.pause()
        after_scroll = len([
            c for c in chat.children if isinstance(c, VerticalScroll)
        ])
    assert after_scroll == before_scroll  # no new VerticalScroll added


@pytest.mark.asyncio
async def test_show_panel_routes_scrollable_to_tui_chat():
    """tasklist._show should wrap panels in a scrollable block in TUI mode."""
    from rich.panel import Panel
    from textual.containers import VerticalScroll

    from aru.runtime import init_ctx, set_ctx
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatPane

    app = AruApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        ctx = init_ctx()
        ctx.tui_app = app
        set_ctx(ctx)
        chat = app.query_one(ChatPane)
        from aru.tools.tasklist import _show
        panel = Panel("hello " * 200, title="big")
        before = len(list(chat.children))
        _show(panel)
        await pilot.pause(0.2)
        after_children = list(chat.children)
        has_scroll = any(
            isinstance(c, VerticalScroll) for c in after_children
        )
    assert len(after_children) > before
    assert has_scroll
