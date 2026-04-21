"""Regression: StatusPane reflects session.estimated_cost after tokens update."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_status_reads_estimated_cost_not_total_cost():
    """Session exposes ``estimated_cost``, not ``total_cost``. The
    StatusPane must pick up the right attribute when refreshing."""
    from aru.session import Session
    from aru.tui.app import AruApp
    from aru.tui.widgets.status import StatusPane

    session = Session()
    # Simulate an accounted turn on an Anthropic-priced model.
    session.model_ref = "anthropic/claude-sonnet-4-5-20250929"
    session.total_input_tokens = 1_000_000
    session.total_output_tokens = 500_000

    app = AruApp(session=session)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusPane)
        status._refresh_from_session()
        await pilot.pause()
    # estimated_cost > 0 — we've consumed real tokens.
    assert status.total_cost > 0.0


@pytest.mark.asyncio
async def test_status_cost_updates_after_turn_end_refresh():
    """The belt-and-suspenders refresh in _run_turn must pick the cost up."""
    from aru.session import Session
    from aru.tui.app import AruApp
    from aru.tui.widgets.status import StatusPane

    session = Session()
    session.model_ref = "anthropic/claude-sonnet-4-5-20250929"

    app = AruApp(session=session)
    async with app.run_test() as pilot:
        await pilot.pause()
        status = app.query_one(StatusPane)
        # Initially zero.
        assert status.total_cost == 0.0
        # Simulate tokens arriving via track_tokens path.
        session.total_input_tokens = 2000
        session.total_output_tokens = 800
        status._refresh_from_session()
        await pilot.pause()
    assert status.total_cost > 0.0
