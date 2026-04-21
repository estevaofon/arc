"""Aru-branded header widget for the TUI (visual polish)."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static


class AruHeader(Static):
    """Thin branded header — logo + subtitle + hint."""

    DEFAULT_CSS = """
    AruHeader {
        dock: top;
        height: 3;
        background: $primary;
        color: $text;
        padding: 0 2;
        content-align: center middle;
    }
    """

    def __init__(self, *, subtitle: str = "") -> None:
        super().__init__()
        self._subtitle = subtitle

    def on_mount(self) -> None:
        self.update(self._compose_text())

    def set_subtitle(self, text: str) -> None:
        self._subtitle = text
        self.update(self._compose_text())

    def _compose_text(self) -> Text:
        t = Text()
        t.append("▶ aru", style="bold white")
        t.append(" · ", style="dim")
        t.append("agentic cli", style="italic dim white")
        if self._subtitle:
            t.append("  —  ", style="dim")
            t.append(self._subtitle, style="white")
        return t
