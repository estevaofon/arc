"""Task list and plan steps panels render into the TUI ChatPane."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_create_task_list_renders_panel_in_chat():
    """`create_task_list` should surface its Rich panel inside the ChatPane
    when running in TUI mode — verified by checking the SVG render."""
    from aru.runtime import init_ctx, set_ctx
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatPane
    import re, html

    app = AruApp()
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        ctx = init_ctx()
        ctx.tui_app = app
        set_ctx(ctx)
        chat = app.query_one(ChatPane)
        before = len(list(chat.children))
        from aru.tools.tasklist import create_task_list
        create_task_list(["first thing", "second thing"])
        await pilot.pause(0.3)
        after = len(list(chat.children))
        svg = app.export_screenshot()
        text = "".join(
            html.unescape(m) for m in
            re.findall(r"<text[^>]*>([^<]*)</text>", svg, re.DOTALL)
        ).replace("\xa0", " ")
    # A new renderable was mounted, and at least part of the task text is
    # visible in the viewport.
    assert after > before
    assert "first thing" in text or "second thing" in text


@pytest.mark.asyncio
async def test_flush_plan_render_pushes_to_chat():
    """When the session flags `_plan_render_pending`, flush_plan_render
    should emit the plan panel into the ChatPane (via ``_show``)."""
    from aru.runtime import init_ctx, set_ctx
    from aru.session import Session
    from aru.tools.tasklist import flush_plan_render
    from aru.tui.app import AruApp

    app = AruApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        ctx = init_ctx()
        ctx.tui_app = app
        session = Session()
        session.set_plan(task="Test plan", plan_content="## Steps\n1. one\n2. two")
        session._plan_render_pending = True
        ctx.session = session
        set_ctx(ctx)
        # Count children before.
        before = len(list(app.query_one("ChatPane").children))
        flush_plan_render(session)
        await pilot.pause(0.2)
        after = len(list(app.query_one("ChatPane").children))
    # Panel was mounted into chat.
    assert after > before


@pytest.mark.asyncio
async def test_sink_on_tool_batch_finished_flushes_plan():
    """TextualBusSink.on_tool_batch_finished must call flush_plan_render."""
    from aru.runtime import init_ctx, set_ctx
    from aru.session import Session
    from aru.tui.app import AruApp
    from aru.tui.sinks import TextualBusSink
    from aru.tui.widgets.chat import ChatPane

    app = AruApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        ctx = init_ctx()
        ctx.tui_app = app
        session = Session()
        session.set_plan(task="Another", plan_content="## Steps\n1. a\n2. b")
        session._plan_render_pending = True
        ctx.session = session
        set_ctx(ctx)
        chat = app.query_one(ChatPane)
        before = len(list(chat.children))
        sink = TextualBusSink(app=app, chat_pane=chat)
        sink.on_tool_batch_finished(session=session)
        await pilot.pause(0.2)
        after = len(list(chat.children))
    assert after > before
