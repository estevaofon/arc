"""ChatPane — streaming chat view for the TUI (E3b).

Renders user + assistant messages as stacked ``ChatMessageWidget``s inside
a scrollable container. Each assistant message has a ``reactive`` buffer
that is updated incrementally by the ``TextualBusSink`` as content
deltas arrive from the Agno stream.

Design (per plan-reviewer):

* NO mutation of ``RichLog.lines[-1]`` — that is Textual internal state.
* Each assistant message is its own widget with a reactive ``buffer``;
  Textual's reactive system re-renders when it changes.
* ``set_interval(0.05, _flush)`` debounces rapid content deltas so we
  don't re-render on every single token.
* Tool calls show inline with a cycling indicator that flips to a check
  when the tool completes.
"""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static


class ChatMessageWidget(Static):
    """A single chat message — user, assistant, system, or tool call.

    Inherits from ``Static`` (not ``Widget``) so Textual's native text
    selection path works: ``Static`` participates in the selection
    traversal that ``Screen.get_selected_text`` uses. Click + drag to
    select, Ctrl+C to copy.
    """

    # Explicit — any refactor that disables this would silently break
    # copy-via-mouse.
    ALLOW_SELECT: bool = True

    DEFAULT_CSS = """
    ChatMessageWidget {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
    }
    ChatMessageWidget.user {
        color: $accent;
        background: $boost;
        padding: 0 1;
    }
    ChatMessageWidget.assistant {
        color: $text;
        /* No inner max-height — long code blocks and replies flow into
           the ChatPane's own scroll, matching OpenCode's behaviour.
           The whole conversation scrolls as one, so the user can see
           every line of a big file dump without hunting for a nested
           scrollbar inside an assistant bubble. */
    }
    ChatMessageWidget.system {
        color: $text-muted;
        text-style: italic;
        margin-top: 0;
        margin-bottom: 0;
    }
    ChatMessageWidget.tool {
        color: $success;
        padding-top: 0;
        padding-bottom: 0;
        padding-left: 3;
        padding-right: 0;
        margin-top: 0;
        margin-bottom: 0;
        margin-left: 0;
        margin-right: 0;
        height: 1;
    }
    ChatMessageWidget.tool.pending {
        color: $warning;
    }
    """

    buffer: reactive[str] = reactive("", layout=True)

    def __init__(
        self,
        *,
        role: str = "assistant",
        initial: str = "",
        tool_state: str = "",
    ) -> None:
        # Pass empty renderable to Static; watch_buffer does the real work.
        super().__init__("")
        self.role = role
        self.tool_state = tool_state
        self.set_reactive(ChatMessageWidget.buffer, initial)
        if role in ("user", "assistant", "system", "tool"):
            self.add_class(role)
        if role == "tool" and tool_state == "pending":
            self.add_class("pending")
        # Paint the initial render so the widget shows something before
        # the first reactive watcher fires.
        self.update(self._compose_renderable())

    def watch_buffer(self, _old: str, _new: str) -> None:
        # Reactive watcher — repaint the Static when the buffer changes.
        self.update(self._compose_renderable())

    def _compose_renderable(self) -> Any:
        text = self.buffer
        if self.role == "user":
            return Text(f"> {text}", style="bold")
        if self.role == "assistant":
            # Render markdown through Rich's real engine, then flatten
            # the resulting Segment stream into one Text so Textual's
            # selection traversal (click+drag, Ctrl+C) still walks the
            # characters. Rich's ``Markdown`` used directly is a Group
            # composite that selection skips — flattening preserves the
            # visual polish (bold, headings, bullets, code highlight)
            # without that limitation.
            if not text:
                return Text("")
            width = max(self.size.width or 100, 20)
            return _markdown_to_text(text, width=width)
        if self.role == "tool":
            icon = "✓" if self.tool_state == "done" else "↻"
            return Text(f"{icon} {text}")
        # system
        return Text(text)

    def on_resize(self, event) -> None:
        """Re-render the assistant bubble so markdown wrap follows width.

        Without this, segments are frozen at whatever width was live when
        the buffer last changed — a subsequent resize (toggling sidebar,
        making the terminal wider) would leave wrap decisions stale.
        """
        if self.role == "assistant" and self.buffer:
            try:
                self.update(self._compose_renderable())
            except Exception:
                pass


def _markdown_to_text(raw: str, width: int = 100) -> Text:
    """Render markdown via Rich, flatten the output into a selectable ``Text``.

    Uses Rich's real ``Markdown`` engine (bold, headings, bullets, fenced
    code with syntax highlighting, links), then walks the resulting
    ``Segment`` stream and rebuilds a single flat ``Text``. The flat
    shape is what Textual's ``get_selected_text`` path traverses, so
    click+drag inside an assistant bubble still selects arbitrary
    ranges. Rich's ``Markdown`` as a renderable is a composite
    (``Group`` of ``Panel``/``Syntax``/…) that bypasses that traversal —
    this function avoids that by converting structure→style while
    keeping the text linear.

    Width is passed explicitly so wrap decisions match the current pane
    width. On resize, ``ChatMessageWidget.on_resize`` re-calls this so
    the styling follows.
    """
    if not raw:
        return Text("")
    console = Console(
        file=io.StringIO(),
        width=max(width, 20),
        color_system="truecolor",
        force_terminal=True,
        legacy_windows=False,
    )
    try:
        options = console.options.update(width=max(width, 20))
        segments = list(console.render(Markdown(raw), options))
    except Exception:
        # Defensive: Rich should never raise on arbitrary text, but if
        # streaming ever hands us something pathological mid-delta, fall
        # back to plain so the stream keeps flowing.
        return Text(raw)
    out = Text()
    for seg in segments:
        if seg.text:
            out.append(seg.text, style=seg.style or "")
    return out


class ChatPane(VerticalScroll):
    """Scrollable container of chat messages (E3b)."""

    ALLOW_SELECT: bool = True

    DEFAULT_CSS = """
    ChatPane {
        background: $surface;
        padding: 1;
    }
    """

    # Debounce window for content-delta flushing (seconds). Chosen so
    # 20+ tokens/s still looks smooth without burning CPU on every byte.
    DEBOUNCE_SEC: float = 0.05

    def __init__(self) -> None:
        super().__init__()
        self._active_assistant: ChatMessageWidget | None = None
        self._pending_delta: str = ""
        # Tool widgets keyed by tool_id so completion flips the same line.
        self._tool_widgets: dict[str, ChatMessageWidget] = {}

    def on_mount(self) -> None:
        # Periodic flush; cheap because the reactive watcher already
        # debounces repaints when buffer doesn't actually change.
        self.set_interval(self.DEBOUNCE_SEC, self._flush_pending_delta)

    # ── API used by TextualBusSink and the App ────────────────────────

    def add_user_message(self, text: str) -> None:
        self._close_active_assistant()
        self._active_assistant = None
        self.mount(ChatMessageWidget(role="user", initial=text))
        self.scroll_end(animate=False)

    def add_system_message(self, text: str) -> None:
        self._close_active_assistant()
        self.mount(ChatMessageWidget(role="system", initial=text))
        self.scroll_end(animate=False)

    def add_renderable(
        self,
        renderable: Any,
        *,
        scrollable: bool = False,
        max_height: int = 20,
    ) -> None:
        """Mount an arbitrary Rich renderable (e.g. the ASCII logo).

        When ``scrollable=True``, the renderable is wrapped in a
        ``VerticalScroll`` that grows with its content up to
        ``max_height`` lines and only engages the inner scrollbar when
        the panel overflows. Using a hard ``height`` instead would
        reserve 20 blank rows under every small panel (task list with 2
        subtasks, one-liner plan status, etc.), which is the "giant
        margin above and below" the user sees.
        """
        from textual.widgets import Static
        self._close_active_assistant()
        widget = Static(renderable)
        if scrollable:
            from textual.containers import VerticalScroll
            wrapper = VerticalScroll()
            # Auto height so a 3-line panel occupies 3 rows, not 20.
            # The cap only kicks in for truly big content (long diffs,
            # file previews) where the scrollbar is the whole point.
            wrapper.styles.height = "auto"
            wrapper.styles.max_height = max_height
            # Kill any container padding/margin — the Rich panel already
            # has its own border+padding and we don't want an extra blank
            # row bleeding outside the box.
            wrapper.styles.padding = 0
            wrapper.styles.margin = 0
            self.mount(wrapper)
            wrapper.mount(widget)
        else:
            self.mount(widget)
        self.scroll_end(animate=False)

    def start_assistant_message(self) -> None:
        """Open a new assistant message to accumulate deltas into."""
        self._close_active_assistant()
        self._active_assistant = ChatMessageWidget(role="assistant", initial="")
        self.mount(self._active_assistant)
        self.scroll_end(animate=False)

    def append_assistant_delta(self, delta: str) -> None:
        """Accumulate content into the active assistant message (debounced)."""
        if self._active_assistant is None:
            self.start_assistant_message()
        self._pending_delta += delta

    def finalize_assistant_message(self, final: str | None = None) -> None:
        """Flush any buffered delta and close the message."""
        self._flush_pending_delta()
        if self._active_assistant is not None and final is not None:
            self._active_assistant.buffer = final
        self._active_assistant = None
        self.scroll_end(animate=False)

    def add_tool_call(self, *, tool_id: str, label: str) -> None:
        """Emit an inline 'in-progress' tool entry."""
        widget = ChatMessageWidget(role="tool", initial=label, tool_state="pending")
        self._tool_widgets[tool_id] = widget
        self.mount(widget)
        self.scroll_end(animate=False)

    def complete_tool_call(
        self, *, tool_id: str, label: str | None = None, duration_ms: float = 0.0
    ) -> None:
        """Flip a previously-emitted tool entry to the 'done' state."""
        widget = self._tool_widgets.pop(tool_id, None)
        if widget is None:
            # We never saw the start event — emit a retroactive complete line
            widget = ChatMessageWidget(
                role="tool", initial=(label or tool_id), tool_state="done"
            )
            self.mount(widget)
            self.scroll_end(animate=False)
            return
        # Update label if caller gave a richer one; flip state classes.
        if label:
            widget.buffer = (
                f"{label} ({duration_ms/1000:.1f}s)"
                if duration_ms >= 500
                else label
            )
        widget.tool_state = "done"
        widget.remove_class("pending")
        widget.refresh(layout=True)

    # ── internals ─────────────────────────────────────────────────────

    def _flush_pending_delta(self) -> None:
        if self._pending_delta and self._active_assistant is not None:
            self._active_assistant.buffer = (
                self._active_assistant.buffer + self._pending_delta
            )
            self._pending_delta = ""
            self.scroll_end(animate=False)

    def _close_active_assistant(self) -> None:
        self._flush_pending_delta()
        self._active_assistant = None
