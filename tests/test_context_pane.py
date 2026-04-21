"""ContextPane renders 'Last context window: N' once tokens land."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_context_pane_shows_last_context_window_value():
    from aru.session import Session
    from aru.tui.app import AruApp
    from aru.tui.widgets.context_pane import ContextPane

    session = Session()
    session.model_ref = "anthropic/claude-sonnet-4-5-20250929"
    session.last_input_tokens = 7_483
    session.last_output_tokens = 662
    session.last_cache_read = 13_631
    session.last_cache_write = 0
    session.total_input_tokens = 7_483
    session.total_output_tokens = 662

    app = AruApp(session=session)
    async with app.run_test(size=(160, 50)) as pilot:
        await pilot.pause()
        ctx_pane = app.query_one(ContextPane)
        ctx_pane.refresh_from_session()
        await pilot.pause()
        import re, html
        svg = app.export_screenshot()
        text = "".join(
            html.unescape(m) for m in
            re.findall(r"<text[^>]*>([^<]*)</text>", svg, re.DOTALL)
        ).replace("\xa0", " ")
    expected_total = 7_483 + 662 + 13_631  # 21,776
    assert "Last context window" in text
    assert f"{expected_total:,}" in text  # "21,776"


@pytest.mark.asyncio
async def test_context_pane_waiting_state_when_no_tokens():
    from aru.session import Session
    from aru.tui.app import AruApp
    from aru.tui.widgets.context_pane import ContextPane

    session = Session()  # zero tokens

    app = AruApp(session=session)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        ctx_pane = app.query_one(ContextPane)
        ctx_pane.refresh_from_session()
        await pilot.pause()
        import re, html
        svg = app.export_screenshot()
        text = "".join(
            html.unescape(m) for m in
            re.findall(r"<text[^>]*>([^<]*)</text>", svg, re.DOTALL)
        ).replace("\xa0", " ")
    # Text word-wraps at narrow sidebar widths; check the distinctive token.
    assert "Waiting" in text
