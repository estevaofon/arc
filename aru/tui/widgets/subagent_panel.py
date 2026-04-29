"""SubagentPanel — live view of running sub-agents and their current tool.

Mounts between ``ThinkingIndicator`` and ``StatusPane`` in the TUI shell.
Hidden when no sub-agent is active; expands to one row per running or
just-completed sub-agent during a fan-out.

Why this exists: a multi-``delegate_task`` turn (e.g. 3 explorers fanning
out in parallel) renders as a stream of interleaved ``[explorer-3] ✓ grep
0.2s`` lines in the chat — informationally poor when you want to see at a
glance which workers are alive, what each is doing right now, and how
many have finished. ``BackgroundTaskStatus.tsx`` in claude-code does the
same thing with horizontal pills; we go vertical because TUI rows are
cheaper than horizontal real estate.

Inputs (subscribed by ``AruApp._install_bus_subscriptions``):

* ``subagent.start``           → mount a row, store agent_name + start time
* ``subagent.tool.started``    → fill row's "current tool" cell
* ``subagent.tool.completed``  → clear current tool back to "thinking…"
* ``subagent.complete``        → flip row to done/error/cancelled, schedule fade

The panel does not own any execution logic — it is a pure observer of the
plugin event bus. ``delegate.py`` is the publisher.
"""

from __future__ import annotations

import hashlib
import time

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static


# Stable palette for agent rows. A row's color is a deterministic hash of
# its agent_name so the same sub-agent keeps the same color across its
# lifetime, and a fan-out of 3 explorers (Explorer-1 / Explorer-2 /
# Explorer-3) almost certainly lands on three different colors because
# the sha256 of distinct strings spreads out over the 8-slot palette.
_PALETTE = (
    "cyan",
    "magenta",
    "yellow",
    "green",
    "blue",
    "red",
    "bright_cyan",
    "bright_magenta",
)


def _color_for(name: str) -> str:
    """Deterministic palette pick keyed on the agent's display name."""
    if not name:
        return _PALETTE[0]
    h = int(hashlib.sha256(name.encode("utf-8", "replace")).hexdigest(), 16)
    return _PALETTE[h % len(_PALETTE)]


def _fmt_dur(seconds: float) -> str:
    """Human-friendly duration: ``320ms`` / ``1.4s`` / ``2m07s``."""
    if seconds < 1:
        return f"{int(seconds * 1000)}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds - minutes * 60)
    return f"{minutes}m{secs:02d}s"


class SubagentPanel(VerticalScroll):
    """One row per active or just-completed sub-agent.

    Rows are keyed by ``task_id``. Running rows poll their elapsed time
    via a per-tick refresh; completed rows fade after ``FADE_SECONDS``
    so the panel returns to ``display: none`` once everyone is done.
    """

    DEFAULT_CSS = """
    SubagentPanel {
        display: none;
        max-height: 6;
        background: $boost;
        border-top: solid $primary;
        padding: 0 1;
    }
    SubagentPanel.-busy {
        display: block;
    }
    .subagent-row {
        height: 1;
        padding: 0;
    }
    """

    # How long a completed row stays visible before being removed. Short
    # because completion is celebratory but not load-bearing — the user
    # already saw the result land in the chat.
    FADE_SECONDS: float = 3.0
    # How often to repaint running rows (live elapsed time).
    TICK_SECONDS: float = 0.2

    def __init__(self) -> None:
        super().__init__()
        # task_id -> {
        #   "agent": str,          # display name (e.g. "Explorer-3")
        #   "parent": str | None,  # parent_id from start event (truthy => nested)
        #   "start": float,        # monotonic
        #   "done": bool,
        #   "done_at": float | None,
        #   "status": str,         # "running" | "ok" | "error" | "cancelled"
        #   "current_tool": str,   # cleared between tool start/completed pairs
        #   "tool_args": str,      # short preview shown next to the tool name
        #   "tokens_in": int,
        #   "tokens_out": int,
        #   "widget": Static,
        # }
        self._rows: dict[str, dict] = {}

    def on_mount(self) -> None:
        self.set_interval(self.TICK_SECONDS, self._tick)

    # ── Bus callbacks ────────────────────────────────────────────────

    def on_subagent_start(self, payload: dict) -> None:
        task_id = str(payload.get("task_id") or "")
        if not task_id or task_id in self._rows:
            return
        widget = Static(classes="subagent-row")
        self._rows[task_id] = {
            "agent": str(payload.get("agent_name") or "subagent"),
            "parent": payload.get("parent_id"),
            "start": time.monotonic(),
            "done": False,
            "done_at": None,
            "status": "running",
            "current_tool": "",
            "tool_args": "",
            "tokens_in": 0,
            "tokens_out": 0,
            "widget": widget,
        }
        self.mount(widget)
        self.add_class("-busy")
        self._render_row(task_id)

    def on_subagent_tool_started(self, payload: dict) -> None:
        task_id = str(payload.get("task_id") or "")
        row = self._rows.get(task_id)
        if row is None:
            return
        row["current_tool"] = str(payload.get("tool_name") or "")
        row["tool_args"] = str(payload.get("tool_args_preview") or "")
        self._render_row(task_id)

    def on_subagent_tool_completed(self, payload: dict) -> None:
        task_id = str(payload.get("task_id") or "")
        row = self._rows.get(task_id)
        if row is None:
            return
        # Clear the tool slot — the next start (if any) overwrites it.
        # Until then the row reads "thinking…", matching the model's
        # actual state between tool calls.
        row["current_tool"] = ""
        row["tool_args"] = ""
        self._render_row(task_id)

    def on_subagent_complete(self, payload: dict) -> None:
        task_id = str(payload.get("task_id") or "")
        row = self._rows.get(task_id)
        if row is None:
            return
        row["done"] = True
        row["done_at"] = time.monotonic()
        # Normalise status: delegate emits "completed" on the happy path
        # but the schema's enum is "ok | error | cancelled". Either way
        # we render the "done" check unless explicitly error/cancelled.
        raw_status = str(payload.get("status") or "ok")
        row["status"] = raw_status
        row["tokens_in"] = int(payload.get("tokens_in") or 0)
        row["tokens_out"] = int(payload.get("tokens_out") or 0)
        self._render_row(task_id)

    # ── Periodic tick: refresh running durations and reap completed ──

    def _tick(self) -> None:
        now = time.monotonic()
        to_remove: list[str] = []
        for task_id, row in list(self._rows.items()):
            if not row["done"]:
                self._render_row(task_id)
            else:
                done_at = row["done_at"] or now
                if now - done_at > self.FADE_SECONDS:
                    to_remove.append(task_id)
        for task_id in to_remove:
            row = self._rows.pop(task_id)
            try:
                row["widget"].remove()
            except Exception:
                # Widget may already be detached if the App is shutting
                # down — never let the tick raise.
                pass
        if not self._rows:
            self.remove_class("-busy")

    # ── Rendering ─────────────────────────────────────────────────────

    def _render_row(self, task_id: str) -> None:
        row = self._rows.get(task_id)
        if row is None:
            return
        widget = row["widget"]
        elapsed = time.monotonic() - row["start"]

        if not row["done"]:
            icon, icon_style = "↻", "bold cyan"
        elif row["status"] in ("ok", "completed"):
            icon, icon_style = "✓", "bold green"
        elif row["status"] == "cancelled":
            icon, icon_style = "⊘", "bold yellow"
        else:
            icon, icon_style = "✗", "bold red"

        agent_color = _color_for(row["agent"])
        depth_prefix = "↳ " if row["parent"] else ""
        parts: list[tuple[str, str]] = [
            (f"{icon} ", icon_style),
            (f"{depth_prefix}@{row['agent']}", f"bold {agent_color}"),
            ("  ", ""),
        ]

        if row["done"]:
            tok = f"{_fmt_num(row['tokens_in'])}↓/{_fmt_num(row['tokens_out'])}↑"
            label = {
                "ok": "done",
                "completed": "done",
                "cancelled": "cancelled",
                "error": "error",
            }.get(row["status"], row["status"])
            parts.append((f"{label} · {tok}", "dim"))
        elif row["current_tool"]:
            tool_label = row["current_tool"]
            if row["tool_args"]:
                tool_label = f"{tool_label} {row['tool_args'][:40]}"
            parts.append((tool_label, "white"))
        else:
            parts.append(("thinking…", "italic dim"))

        parts.append((f"  {_fmt_dur(elapsed)}", "dim"))
        try:
            widget.update(Text.assemble(*parts))
        except Exception:
            # Widget can be unmounted between tick iterations on shutdown.
            pass

    # ── Test / introspection helpers ─────────────────────────────────

    def active_task_ids(self) -> list[str]:
        """Return task_ids currently tracked (running or fading)."""
        return list(self._rows.keys())

    def is_busy(self) -> bool:
        return self.has_class("-busy")


def _fmt_num(n: int) -> str:
    """Compact token count: 12345 → ``12.3K``."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)
