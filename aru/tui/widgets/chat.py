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

from typing import Any

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

    # Cap assistant messages at this many lines — beyond this they
    # become scrollable inside their own bubble instead of pushing the
    # whole conversation down.
    ASSISTANT_MAX_HEIGHT: int = 30

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
        /* Long replies keep a generous cap before we wrap them in a
           VerticalScroll (see ChatPane.add_renderable for scrollable
           panels). Selection works natively on Static — no overflow
           override here so mouse drag selects instead of scrolling. */
        max-height: 60;
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
            # Rich's ``Markdown`` produces a composite renderable whose
            # content isn't visited by Textual's selection traversal, so
            # users could not copy assistant replies with click+drag.
            # Rendering as plain ``Text`` keeps selection fully working;
            # lightweight markdown cues (headers, code fences) are
            # styled via ``_markdown_to_text`` so the visual loss is
            # small but selection is universal.
            return _markdown_to_text(text) if text else Text("")
        if self.role == "tool":
            icon = "✓" if self.tool_state == "done" else "↻"
            return Text(f"{icon} {text}")
        # system
        return Text(text)


def _markdown_to_text(raw: str) -> Text:
    """Best-effort, selection-friendly rendering of markdown-ish content.

    Walks the markdown line-by-line and applies Rich styles in place:

    * ``#`` / ``##`` / ``###`` headers → bold cyan
    * Inline backticks → monospace-ish dim
    * Fenced code blocks (```lang ... ```) → dim green
    * Bullet markers (``-`` / ``*`` / ``1.``) kept verbatim

    No AST, no ``rich.markdown.Markdown`` → the output is a flat
    ``Text`` whose characters live in Textual's selection traversal.
    """
    out = Text()
    in_code_block = False
    for i, line in enumerate(raw.split("\n")):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            out.append(line + "\n", style="dim green")
            continue
        if in_code_block:
            out.append(line + "\n", style="green")
            continue
        if stripped.startswith("### "):
            out.append(line + "\n", style="bold white")
            continue
        if stripped.startswith("## "):
            out.append(line + "\n", style="bold cyan")
            continue
        if stripped.startswith("# "):
            out.append(line + "\n", style="bold bright_cyan")
            continue
        # Inline backticks → dim highlighting while keeping the chars.
        if "`" in line:
            _append_with_backticks(out, line + "\n")
            continue
        out.append(line + "\n")
    return out


def _append_with_backticks(out: Text, line: str) -> None:
    """Append ``line`` to ``out``, styling backtick-enclosed spans."""
    parts = line.split("`")
    for idx, part in enumerate(parts):
        if idx % 2 == 1:
            out.append(f"`{part}`", style="bold dim yellow")
        else:
            if part:
                out.append(part)


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
        ``VerticalScroll`` with a fixed ``max_height`` so file contents,
        long diffs, and big panels (e.g. newly created file previews)
        don't push the rest of the conversation out of the viewport —
        the user can scroll *inside* the block independently.
        """
        from textual.widgets import Static
        self._close_active_assistant()
        widget = Static(renderable)
        if scrollable:
            from textual.containers import VerticalScroll
            wrapper = VerticalScroll()
            # Use `height` (not max-height) so the wrapper reserves an
            # exact slot; Textual renders a scrollbar automatically when
            # the child overflows.
            wrapper.styles.height = max_height
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
