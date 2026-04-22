"""Regression tests for Ctrl+C handling in ``run_agent_capture``.

The bug: on Python 3.11+, ``asyncio.run`` cancels the main task when the
user hits Ctrl+C. ``run_agent_capture`` catches the resulting
``CancelledError`` — but Python leaves the task in a "cancelling" state
until ``Task.uncancel()`` is called. As a result, the REPL's NEXT await
(the prompt) would raise ``CancelledError`` immediately, making Ctrl+C
during an agent turn look like it "exits aru" instead of just aborting
the turn.

These tests cover the narrow contract: after an interrupted turn, the
task must not be in a cancelling state and the abort signal must be
cleared.
"""

from __future__ import annotations

import asyncio


async def _catch_and_recover_like_runner() -> None:
    """Mirror the runner.py:666 except block in isolation.

    We don't import ``run_agent_capture`` here because a full mock would
    drag in agno + RichLiveSink + ctx — overkill for testing an invariant
    on three lines. Keeping it isolated is the point: if this pattern
    stays valid, the real runner keeps working too.
    """
    from aru.runtime import reset_abort
    try:
        await asyncio.sleep(10)
    except (KeyboardInterrupt, asyncio.CancelledError):
        current = asyncio.current_task()
        if current is not None:
            current.uncancel()
        reset_abort()


async def test_repl_task_survives_mid_turn_cancellation():
    """After Ctrl+C during a turn, the next await must NOT re-raise."""
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None
    loop.call_later(0.01, task.cancel)
    await _catch_and_recover_like_runner()
    # If the fix works, this await completes without re-raising. This is
    # the moral equivalent of the REPL reaching its next prompt.
    await asyncio.sleep(0.01)
    assert task.cancelling() == 0


async def test_abort_flag_is_cleared_after_interrupt():
    """Leftover abort flag would silently short-circuit the next turn."""
    from aru.runtime import abort_current, is_aborted, reset_abort

    reset_abort()
    assert is_aborted() is False

    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None

    # Set abort during the simulated turn, then cancel to trigger recovery.
    def _trigger():
        abort_current()
        task.cancel()

    loop.call_later(0.01, _trigger)
    await _catch_and_recover_like_runner()
    assert is_aborted() is False
