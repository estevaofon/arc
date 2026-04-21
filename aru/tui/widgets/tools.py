"""ToolsPane — live sidebar of active/completed tool calls (E4)."""

from __future__ import annotations

import time
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Label, Static


class ToolsPane(VerticalScroll):
    """Live feed of tool invocations.

    Rows are keyed by ``tool_id``. A started tool shows a cycling arrow and
    live elapsed; on completion the arrow flips to a check + final
    duration. Completed rows fade after a short timeout so the pane stays
    focused on the current batch, matching the ephemeral feel of Claude
    Code's tool indicators.
    """

    DEFAULT_CSS = """
    ToolsPane {
        background: $surface;
        border-left: solid $primary;
        padding: 0 1;
        min-width: 28;
        width: 1fr;
    }
    #tools-header {
        color: $accent;
        text-style: bold;
        padding-bottom: 1;
    }
    .tool-row {
        height: auto;
        padding: 0;
        color: $text;
    }
    .tool-row.running {
        color: $warning;
    }
    .tool-row.done {
        color: $success;
    }
    .tool-row.error {
        color: $error;
    }
    """

    # How long a completed row stays visible before being removed.
    FADE_SECONDS: float = 8.0
    # How often we refresh the "live elapsed" field of running tools.
    TICK_SECONDS: float = 0.2

    def __init__(self) -> None:
        super().__init__()
        # tool_id -> {"label": str, "start": float, "done": bool,
        #             "done_at": float | None, "widget": Static}
        self._rows: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        yield Label("Tools", id="tools-header")

    def on_mount(self) -> None:
        self.set_interval(self.TICK_SECONDS, self._tick)

    # ── Bus callbacks ───────────────────────────────────────────────

    def on_tool_called(self, payload: dict) -> None:
        tool_id = str(payload.get("tool_id") or "")
        if not tool_id or tool_id in self._rows:
            return
        label = str(
            payload.get("label")
            or payload.get("tool_name")
            or "tool"
        )
        widget = Static(classes="tool-row running")
        self._rows[tool_id] = {
            "label": label,
            "start": time.monotonic(),
            "done": False,
            "done_at": None,
            "widget": widget,
        }
        self.mount(widget)
        self._render_row(tool_id)
        self.scroll_end(animate=False)

    def on_tool_completed(self, payload: dict) -> None:
        tool_id = str(payload.get("tool_id") or "")
        row = self._rows.get(tool_id)
        if row is None:
            # Never saw the start event — emit a synthetic "done" entry.
            label = str(payload.get("tool_name") or tool_id or "tool")
            widget = Static(classes="tool-row done")
            self._rows[tool_id] = {
                "label": label,
                "start": time.monotonic(),
                "done": True,
                "done_at": time.monotonic(),
                "widget": widget,
            }
            self.mount(widget)
            self._render_row(tool_id)
            self.scroll_end(animate=False)
            return

        row["done"] = True
        row["done_at"] = time.monotonic()
        widget = row["widget"]
        widget.remove_class("running")
        widget.add_class("done")
        self._render_row(tool_id)

    # ── Periodic tick refreshes running durations and reaps old rows ──

    def _tick(self) -> None:
        now = time.monotonic()
        to_remove: list[str] = []
        for tool_id, row in list(self._rows.items()):
            if not row["done"]:
                self._render_row(tool_id)
            else:
                done_at = row["done_at"] or now
                if now - done_at > self.FADE_SECONDS:
                    to_remove.append(tool_id)
        for tool_id in to_remove:
            row = self._rows.pop(tool_id)
            try:
                row["widget"].remove()
            except Exception:
                pass

    def _render_row(self, tool_id: str) -> None:
        row = self._rows.get(tool_id)
        if row is None:
            return
        widget = row["widget"]
        elapsed = time.monotonic() - row["start"]
        icon = "✓" if row["done"] else "↻"
        style_icon = "bold green" if row["done"] else "bold cyan"
        dur = self._format_duration(elapsed)
        text = Text.assemble(
            (f"{icon} ", style_icon),
            (row["label"], "white" if not row["done"] else "dim"),
            (f"  {dur}", "dim"),
        )
        widget.update(text)

    @staticmethod
    def _format_duration(seconds: float) -> str:
        if seconds < 1:
            return f"{int(seconds * 1000)}ms"
        if seconds < 60:
            return f"{seconds:.1f}s"
        minutes = int(seconds // 60)
        secs = int(seconds - minutes * 60)
        return f"{minutes}m{secs:02d}s"

    # ── Bulk state utilities ────────────────────────────────────────

    def clear(self) -> None:
        for row in list(self._rows.values()):
            try:
                row["widget"].remove()
            except Exception:
                pass
        self._rows.clear()
