"""SearchScreen — filter ChatPane messages by substring (E8)."""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList
from textual.widgets.option_list import Option


class SearchScreen(ModalScreen[int | None]):
    """Free-text search over the chat history.

    Live filters the list while you type. ``Enter`` dismisses with the
    index of the matched message in the chat pane so the caller can
    scroll to it. ``Esc`` cancels.
    """

    CSS = """
    SearchScreen {
        align: center middle;
    }
    #search-box {
        background: $panel;
        border: round $primary;
        padding: 1 2;
        width: 80;
        max-width: 90%;
        height: auto;
        max-height: 80%;
    }
    #search-title {
        color: $accent;
        text-style: bold;
        margin-bottom: 1;
    }
    OptionList {
        height: auto;
        max-height: 20;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(self, messages: list[tuple[int, str]]) -> None:
        super().__init__()
        # [(chat_pane_index, preview_text), ...]
        self._messages = messages
        self._filtered: list[tuple[int, str]] = list(messages)

    def compose(self) -> ComposeResult:
        with Vertical(id="search-box"):
            yield Label("Search chat", id="search-title")
            yield Input(placeholder="Type to filter…", id="search-input")
            yield OptionList(id="search-results")

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        self._rebuild_list()

    def on_input_changed(self, event: Input.Changed) -> None:
        query = (event.value or "").strip().lower()
        if not query:
            self._filtered = list(self._messages)
        else:
            self._filtered = [
                (i, text) for i, text in self._messages if query in text.lower()
            ]
        self._rebuild_list()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        opts = self.query_one(OptionList)
        idx = opts.highlighted or 0
        if 0 <= idx < len(self._filtered):
            self.dismiss(self._filtered[idx][0])
        else:
            self.dismiss(None)

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected
    ) -> None:
        oid = event.option.id
        if oid is not None:
            try:
                self.dismiss(int(oid))
                return
            except ValueError:
                pass
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _rebuild_list(self) -> None:
        opts = self.query_one(OptionList)
        opts.clear_options()
        for i, text in self._filtered[:200]:  # cap to keep list snappy
            preview = text if len(text) <= 80 else text[:77] + "…"
            opts.add_option(Option(Text(preview), id=str(i)))
