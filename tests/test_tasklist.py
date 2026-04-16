"""Tests for aru.tools.tasklist — particularly idempotent replace semantics.

Matches OpenCode and Claude Code behavior: `create_task_list` is fully
idempotent. Each call REPLACES the prior list. There is no "already created"
refusal; multi-skill and multi-phase workflows can reseed the list freely.
"""

from __future__ import annotations

import pytest

from aru.runtime import get_ctx
from aru.tools.tasklist import create_task_list, reset_task_store, update_task


@pytest.fixture(autouse=True)
def _reset_store():
    reset_task_store()
    yield
    reset_task_store()


class TestCreateTaskListIdempotent:
    def test_first_call_creates_list(self):
        result = create_task_list(["read file", "write test", "run pytest"])
        assert "created (3 subtasks)" in result
        tasks = get_ctx().task_store.get_all()
        assert [t["description"] for t in tasks] == ["read file", "write test", "run pytest"]

    def test_second_call_replaces_list(self):
        create_task_list(["old A", "old B"])

        result = create_task_list(["new 1", "new 2", "new 3"])

        # Return message indicates replacement, not error
        assert "replaced (3 subtasks)" in result
        assert "Error" not in result
        tasks = get_ctx().task_store.get_all()
        assert [t["description"] for t in tasks] == ["new 1", "new 2", "new 3"]

    def test_replacement_resets_status(self):
        create_task_list(["a", "b", "c"])
        update_task(index=1, status="completed")
        update_task(index=2, status="in_progress")

        create_task_list(["x", "y"])

        tasks = get_ctx().task_store.get_all()
        # All statuses should be pending on the new list — stale state gone
        assert all(t["status"] == "pending" for t in tasks)
        assert [t["description"] for t in tasks] == ["x", "y"]

    def test_replacement_renumbers_from_1(self):
        create_task_list(["old 1", "old 2", "old 3", "old 4", "old 5"])

        create_task_list(["new A", "new B"])

        tasks = get_ctx().task_store.get_all()
        indices = [t["index"] for t in tasks]
        assert indices == [1, 2]

    def test_multi_skill_transition_pattern(self):
        """Mirrors the superpowers pattern: brainstorming list -> writing-plans list -> executing-plans list."""
        create_task_list([
            "Explore project context",
            "Ask clarifying questions",
            "Propose approaches",
            "Present design",
            "Write spec",
        ])
        # Complete all brainstorming items
        for i in range(1, 6):
            update_task(index=i, status="completed")

        # Transition: writing-plans seeds its own list
        create_task_list(["Read spec", "Decompose into tasks", "Write plan file"])
        tasks = get_ctx().task_store.get_all()
        assert len(tasks) == 3
        assert tasks[0]["description"] == "Read spec"

        # Transition: executing-plans seeds its own (per-plan-task) list
        create_task_list([
            "Task 1: add dependency",
            "Task 2: write main.py",
            "Task 3: verify",
        ])
        tasks = get_ctx().task_store.get_all()
        assert len(tasks) == 3
        assert tasks[0]["description"] == "Task 1: add dependency"
        assert all(t["status"] == "pending" for t in tasks)


class TestCreateTaskListValidation:
    def test_empty_list_rejected(self):
        result = create_task_list([])
        assert result.startswith("Error: Minimum 1 subtask required")

    def test_too_many_rejected(self):
        result = create_task_list([f"task {i}" for i in range(1, 12)])
        assert "Maximum" in result
        assert "Error" in result


class TestUpdateTaskRequiresList:
    """update_task should still require a list to exist — replacement semantics
    apply to create_task_list only."""

    def test_update_without_list_errors(self):
        result = update_task(index=1, status="completed")
        assert "Error" in result

    def test_update_after_create_works(self):
        create_task_list(["a", "b"])
        result = update_task(index=1, status="completed")
        assert "Error" not in result
        tasks = get_ctx().task_store.get_all()
        assert tasks[0]["status"] == "completed"
