"""StatusPane — reactive status bar for the TUI (E5).

Shows session id · model · token totals · cost · permission mode · cwd.
Updates via reactive fields driven by subscriptions to the plugin bus:

* ``turn.end``              → tokens in/out, total cost
* ``permission.mode.changed`` → mode badge
* ``cwd.changed``           → worktree indicator

Anything not captured by the bus (session id, model) comes from the
``session`` handed in at mount and is refreshed on each turn-end because
the user may switch models mid-session.
"""

from __future__ import annotations

import os
from typing import Any

from rich.text import Text
from textual.reactive import reactive
from textual.widgets import Static


class StatusPane(Static):
    """Single-line status bar displayed at the bottom of the App."""

    DEFAULT_CSS = """
    StatusPane {
        background: $boost;
        color: $text-muted;
        height: 1;
        padding: 0 1;
    }
    """

    # Reactive fields — any change triggers a re-render via watch_*.
    session_id: reactive[str] = reactive("?", layout=False)
    model_display: reactive[str] = reactive("?", layout=False)
    mode: reactive[str] = reactive("default", layout=False)
    tokens_in: reactive[int] = reactive(0, layout=False)
    tokens_out: reactive[int] = reactive(0, layout=False)
    total_cost: reactive[float] = reactive(0.0, layout=False)
    cwd_short: reactive[str] = reactive("", layout=False)
    # Last-call breakdown — mirrors REPL's /cost "Last context window" block.
    last_in: reactive[int] = reactive(0, layout=False)
    last_out: reactive[int] = reactive(0, layout=False)
    last_cache_read: reactive[int] = reactive(0, layout=False)
    last_cache_write: reactive[int] = reactive(0, layout=False)

    def __init__(self, session: Any = None) -> None:
        super().__init__()
        self._session = session
        if session is not None:
            self._refresh_from_session()

    def on_mount(self) -> None:
        self._rerender()

    # ── Public hooks used by AruApp bus wiring ──────────────────────

    def update_from_turn(self, payload: dict) -> None:
        """Callback for the ``turn.end`` subscription.

        The payload only carries last-turn tokens_in/out; everything
        else (cumulative totals, per-call cache breakdown, cost) is
        read straight from the session which has already been updated
        by ``session.track_tokens`` at this point.
        """
        # Prefer the richer session state over the bus payload — the
        # payload fields are a subset and may lag if multiple publishes
        # race.
        self._refresh_from_session()

    def update_from_mode_change(self, payload: dict) -> None:
        self.mode = str(payload.get("new_mode") or "default")

    def update_from_cwd_change(self, payload: dict) -> None:
        new_cwd = payload.get("new_cwd") or ""
        self.cwd_short = os.path.basename(new_cwd) if new_cwd else ""

    # ── Reactive watchers — re-render on any field change ───────────

    def watch_session_id(self, _old: str, _new: str) -> None:
        self._rerender()

    def watch_model_display(self, _old: str, _new: str) -> None:
        self._rerender()

    def watch_mode(self, _old: str, _new: str) -> None:
        self._rerender()

    def watch_tokens_in(self, _old: int, _new: int) -> None:
        self._rerender()

    def watch_tokens_out(self, _old: int, _new: int) -> None:
        self._rerender()

    def watch_total_cost(self, _old: float, _new: float) -> None:
        self._rerender()

    def watch_cwd_short(self, _old: str, _new: str) -> None:
        self._rerender()

    def watch_last_in(self, _old: int, _new: int) -> None:
        self._rerender()

    def watch_last_out(self, _old: int, _new: int) -> None:
        self._rerender()

    def watch_last_cache_read(self, _old: int, _new: int) -> None:
        self._rerender()

    def watch_last_cache_write(self, _old: int, _new: int) -> None:
        self._rerender()

    # ── Rendering ────────────────────────────────────────────────────

    def _refresh_from_session(self) -> None:
        if self._session is None:
            return
        sid = (
            getattr(self._session, "session_id", None)
            or getattr(self._session, "id", None)
            or "?"
        )
        self.session_id = sid[:8]
        # Pull cumulative + per-call token counts (mirrors /cost).
        try:
            self.tokens_in = int(
                getattr(self._session, "total_input_tokens", 0) or 0
            )
            self.tokens_out = int(
                getattr(self._session, "total_output_tokens", 0) or 0
            )
            self.last_in = int(
                getattr(self._session, "last_input_tokens", 0) or 0
            )
            self.last_out = int(
                getattr(self._session, "last_output_tokens", 0) or 0
            )
            self.last_cache_read = int(
                getattr(self._session, "last_cache_read", 0) or 0
            )
            self.last_cache_write = int(
                getattr(self._session, "last_cache_write", 0) or 0
            )
        except Exception:
            pass
        # Prefer Session.model_display (human-friendly) then model_ref, then model_id.
        display = getattr(self._session, "model_display", None)
        if callable(display):
            try:
                display = display()
            except Exception:
                display = None
        self.model_display = (
            display
            or getattr(self._session, "model_ref", None)
            or getattr(self._session, "model_id", None)
            or "?"
        )
        # Cost accumulates across the session. Session exposes this as
        # ``estimated_cost`` (computed property derived from token totals
        # × MODEL_PRICING). ``total_cost`` was the previous name we used
        # in early drafts — retained here as a fallback for safety.
        try:
            cost_attr = (
                getattr(self._session, "estimated_cost", None)
                if hasattr(self._session, "estimated_cost")
                else getattr(self._session, "total_cost", None)
            )
            if callable(cost_attr):
                cost_attr = cost_attr()
            self.total_cost = float(cost_attr or 0.0)
        except Exception:
            self.total_cost = 0.0

    def _rerender(self) -> None:
        # Single line: session · model · cost · mode · cwd.
        # Token breakdown lives in the sidebar ContextPane now — keeping
        # it here would duplicate information and waste a row.
        parts: list[tuple[str, str]] = []
        parts.append((f" {self.session_id} ", "bold cyan"))
        parts.append(("│ ", "dim"))
        parts.append((self.model_display, "white"))
        parts.append(("  │  ", "dim"))
        parts.append((f"${self.total_cost:.4f}", "green"))
        parts.append(("  │  ", "dim"))
        parts.append((self._format_mode(), self._mode_style()))
        if self.cwd_short:
            parts.append(("  │  ", "dim"))
            parts.append((f"📂 {self.cwd_short}", "yellow"))
        self.update(Text.assemble(*parts))

    def _format_tokens(self) -> str:
        return (
            f"{self._fmt_num(self.tokens_in)} in / "
            f"{self._fmt_num(self.tokens_out)} out"
        )

    @staticmethod
    def _fmt_num(n: int) -> str:
        if n >= 1_000_000:
            return f"{n/1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n/1_000:.1f}K"
        return f"{n:,}"

    def _format_mode(self) -> str:
        return {
            "default": "● default",
            "acceptEdits": "◉ accept",
            "yolo": "⚠ yolo",
            "plan": "⏸ plan",
        }.get(self.mode, f"● {self.mode}")

    def _mode_style(self) -> str:
        return {
            "default": "blue",
            "acceptEdits": "green",
            "yolo": "bold red",
            "plan": "yellow",
        }.get(self.mode, "white")
