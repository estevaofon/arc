"""StatusPane shows REPL-style token breakdown (cumulative + last-turn)."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_status_captures_last_call_breakdown():
    from aru.session import Session
    from aru.tui.app import AruApp
    from aru.tui.widgets.status import StatusPane

    session = Session()
    session.model_ref = "anthropic/claude-sonnet-4-5-20250929"
    # Cumulative + last-turn figures (the fields session.track_tokens sets).
    session.total_input_tokens = 50_000
    session.total_output_tokens = 8_000
    session.total_cache_read_tokens = 14_000
    session.total_cache_write_tokens = 600
    session.last_input_tokens = 7_483
    session.last_output_tokens = 662
    session.last_cache_read = 13_631
    session.last_cache_write = 0

    app = AruApp(session=session)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusPane)
        status._refresh_from_session()
        await pilot.pause()

    assert status.last_in == 7_483
    assert status.last_out == 662
    assert status.last_cache_read == 13_631
    assert status.tokens_in == 50_000
    assert status.tokens_out == 8_000
    # Cost > 0 since tokens landed.
    assert status.total_cost > 0.0


@pytest.mark.asyncio
async def test_sidebar_context_pane_shows_breakdown_text():
    """After removing the duplicate in StatusPane, the breakdown lives
    in the sidebar ContextPane. Verify it renders there."""
    from aru.session import Session
    from aru.tui.app import AruApp
    from aru.tui.widgets.context_pane import ContextPane

    session = Session()
    session.model_ref = "anthropic/claude-sonnet-4-5-20250929"
    session.total_input_tokens = 50_000
    session.total_output_tokens = 8_000
    session.last_input_tokens = 7_483
    session.last_output_tokens = 662
    session.last_cache_read = 13_631

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
    # Last-turn breakdown surfaced in the ContextPane (not StatusPane).
    assert "7,483" in text  # input
    assert "662" in text    # output
    assert "cache_read" in text


@pytest.mark.asyncio
async def test_update_from_turn_reads_session_not_payload():
    """turn.end callback should not trust the payload's tokens_in/out
    alone — it re-reads session so the breakdown comes through."""
    from aru.session import Session
    from aru.tui.app import AruApp
    from aru.tui.widgets.status import StatusPane

    session = Session()
    session.model_ref = "anthropic/claude-sonnet-4-5-20250929"
    session.total_input_tokens = 1000
    session.total_output_tokens = 500
    session.last_input_tokens = 700
    session.last_output_tokens = 400
    session.last_cache_read = 300

    app = AruApp(session=session)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusPane)
        # Payload claims different numbers — we should ignore them in
        # favour of the session (single source of truth).
        status.update_from_turn({"tokens_in": 99, "tokens_out": 99})
        await pilot.pause()
    assert status.tokens_in == 1000
    assert status.last_cache_read == 300
