"""Task list tools for structured step execution.

Provides create_task_list and update_task tools that the executor must call
to plan and track subtasks within each plan step. Inspired by Claude Code
and Antigravity's task management approach.
"""

from __future__ import annotations

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from aru.runtime import TaskStore, get_ctx

MAX_SUBTASKS = 10


def reset_task_store() -> None:
    """Reset the task store between executor steps."""
    get_ctx().task_store.reset()


def get_task_store() -> TaskStore:
    """Get the current task store for inspection."""
    return get_ctx().task_store


def _render_task_list(tasks: list[dict]) -> Panel:
    """Render the task list as a Rich panel."""
    lines = []
    for t in tasks:
        if t["status"] == "completed":
            icon = "[bold green]✓[/bold green]"
            style = "dim"
        elif t["status"] == "in_progress":
            icon = "[bold yellow]~[/bold yellow]"
            style = "bold"
        elif t["status"] == "failed":
            icon = "[bold red]✗[/bold red]"
            style = "red"
        else:
            icon = "[dim]○[/dim]"
            style = "dim"
        lines.append(Text.from_markup(f"  {icon} {t['index']}. {t['description']}", style=style))

    return Panel(
        Group(*lines),
        title="[bold cyan]Subtasks[/bold cyan]",
        border_style="cyan",
        expand=True,
    )


def _show(panel: Panel) -> None:
    """Display panel using the active display or console."""
    ctx = get_ctx()
    if ctx.display and hasattr(ctx.display, "show_permission"):
        ctx.display.show_permission(panel)
    elif ctx.live:
        ctx.live.console.print(panel)
    else:
        ctx.console.print(panel)


def create_task_list(tasks: list[str]) -> str:
    """Create a subtask list for the current step. MUST be called before any other tool.

    Define 1-10 concrete subtasks that you will execute in order.
    Each subtask should be a single action (Read, Write, Edit, Run).

    Args:
        tasks: List of subtask descriptions. Min 1, max 10.
               Example: ["Read backend/models.py", "Write backend/auth.py", "Edit backend/main.py — add import", "Run pytest"]
    """
    store = get_ctx().task_store
    if store.is_created:
        return "Error: Task list already created for this step. Use update_task to update subtask status."

    if len(tasks) < 1:
        return "Error: Minimum 1 subtask required."

    if len(tasks) > MAX_SUBTASKS:
        return f"Error: Maximum {MAX_SUBTASKS} subtasks allowed. Got {len(tasks)}. Simplify your plan."

    created = store.create(tasks)
    panel = _render_task_list(created)
    _show(panel)

    task_lines = "\n".join(f"  {t['index']}. {t['description']}" for t in created)
    return f"Task list created ({len(created)} subtasks):\n{task_lines}\n\nNow execute subtask 1."


def update_task(index: int, status: str) -> str:
    """Update the status of a subtask. Call this as you complete each subtask.

    Args:
        index: Subtask number (1-based).
        status: New status — one of: "in_progress", "completed", "failed".
    """
    store = get_ctx().task_store
    if not store.is_created:
        return "Error: No task list exists. Call create_task_list first."

    if status not in ("in_progress", "completed", "failed"):
        return f"Error: Invalid status '{status}'. Use: in_progress, completed, failed."

    updated = store.update(index, status)
    if not updated:
        return f"Error: Subtask {index} not found."

    # Show updated task list
    all_tasks = store.get_all()
    panel = _render_task_list(all_tasks)
    _show(panel)

    # Check if all done
    completed_count = sum(1 for t in all_tasks if t["status"] == "completed")
    failed_count = sum(1 for t in all_tasks if t["status"] == "failed")
    total = len(all_tasks)

    if completed_count + failed_count == total:
        return f"All subtasks finished ({completed_count} completed, {failed_count} failed). Step done. Output a brief summary of what was created/changed."

    # Find next pending subtask
    next_task = next((t for t in all_tasks if t["status"] == "pending"), None)
    if next_task:
        return f"Subtask {index} → {status}. Next: subtask {next_task['index']} — {next_task['description']}"

    return f"Subtask {index} → {status}."


_PLAN_STATUSES = ("in_progress", "completed", "failed", "skipped")


def _render_plan_steps(steps: list) -> Panel:
    """Render the macro plan_steps list as a Rich panel."""
    icons = {
        "completed": "[bold green]\u2713[/bold green]",
        "in_progress": "[bold yellow]~[/bold yellow]",
        "failed": "[bold red]\u2717[/bold red]",
        "skipped": "[dim]\u00b7[/dim]",
    }
    styles = {
        "completed": "dim",
        "in_progress": "bold",
        "failed": "red",
        "skipped": "dim italic",
    }
    lines = []
    for s in steps:
        icon = icons.get(s.status, "[dim]\u25cb[/dim]")
        style = styles.get(s.status, "dim")
        lines.append(Text.from_markup(f"  {icon} {s.index}. {s.description}", style=style))
    return Panel(
        Group(*lines),
        title="[bold cyan]Plan steps[/bold cyan]",
        border_style="cyan",
        expand=True,
    )


def update_plan_step(index: int, status: str) -> str:
    """Update the status of a macro plan step. Call after completing each step of an active plan.

    Use this when a PLAN ACTIVE system reminder is in your context. Mark each
    step as you finish it. Status options:
      - in_progress: starting work on this step
      - completed: step done successfully
      - failed: step could not be completed
      - skipped: step intentionally not executed (no longer needed)

    Args:
        index: Plan step number (1-based, matches the reminder).
        status: New status (in_progress | completed | failed | skipped).
    """
    session = get_ctx().session
    if session is None or not getattr(session, "plan_steps", None):
        return "Error: No active plan. Use enter_plan_mode(task) or /plan to create one first."

    if status not in _PLAN_STATUSES:
        return f"Error: Invalid status '{status}'. Use: {', '.join(_PLAN_STATUSES)}."

    target = next((s for s in session.plan_steps if s.index == index), None)
    if target is None:
        valid = ", ".join(str(s.index) for s in session.plan_steps)
        return f"Error: Plan step {index} not found. Valid indices: {valid}."

    target.status = status
    panel = _render_plan_steps(session.plan_steps)
    _show(panel)

    pending = [s for s in session.plan_steps if s.status == "pending"]
    if not pending:
        done = sum(1 for s in session.plan_steps if s.status == "completed")
        failed = sum(1 for s in session.plan_steps if s.status == "failed")
        skipped = sum(1 for s in session.plan_steps if s.status == "skipped")
        return (
            f"All plan steps finished ({done} completed, {failed} failed, {skipped} skipped). "
            f"Output a brief summary of what was changed."
        )

    next_step = pending[0]
    return f"Step {index} -> {status}. Next: step {next_step.index} - {next_step.description}"
