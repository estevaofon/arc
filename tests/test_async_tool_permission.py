"""Async tools must run permission checks on a worker thread.

Regression: bash / delegate_task are async and execute on the App loop.
Calling the sync ``check_permission`` directly from there would deadlock
the loop whenever a TUI modal needed to appear (the callback can't fire
while the loop is blocked by ``threading.Event.wait()`` inside
``TuiUI._run_modal``). They must ``await asyncio.to_thread(...)`` the
check so the loop stays free.
"""

from __future__ import annotations

import inspect

import pytest


def test_bash_uses_to_thread_for_permission():
    """The bash tool's source must call check_permission via asyncio.to_thread."""
    from aru.tools import shell as shell_mod
    src = inspect.getsource(shell_mod.bash)
    assert "to_thread" in src and "check_permission" in src
    # Ensure the two live in the same statement (not a bare sync call).
    assert "to_thread(" in src
    # Verify no bare `check_permission("bash", ...)` without to_thread prefix.
    for line in src.split("\n"):
        if "check_permission(" in line and "to_thread" not in line \
                and "import" not in line:
            # Helper comments mentioning the name are OK; executable
            # calls to the function must be prefixed by to_thread.
            stripped = line.strip()
            if stripped.startswith("if") or stripped.startswith("return"):
                pytest.fail(
                    f"bash still calls check_permission synchronously: {line!r}"
                )


def test_delegate_task_uses_to_thread_for_permission():
    from aru.tools import delegate as delegate_mod
    src = inspect.getsource(delegate_mod.delegate_task)
    assert "to_thread" in src and "check_permission" in src
    for line in src.split("\n"):
        if "check_permission(" in line and "to_thread" not in line \
                and "import" not in line:
            stripped = line.strip()
            if stripped.startswith("if") or stripped.startswith("return"):
                pytest.fail(
                    f"delegate_task still calls check_permission "
                    f"synchronously: {line!r}"
                )
