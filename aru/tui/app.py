"""Aru Textual App — full TUI shell (E2 + E3b + E4 + E5 + E6a).

Layout::

    ┌────────────────────────────────────────────────────────┐
    │ AruHeader (branded)                                    │
    ├────────────────────────────────────────────┬───────────┤
    │ ChatPane (scrollable, streams assistant)   │ ToolsPane │
    │                                            │  (live)   │
    ├────────────────────────────────────────────┴───────────┤
    │ StatusPane (session · model · tokens · cost · mode)    │
    ├────────────────────────────────────────────────────────┤
    │ Input (type & Enter → dispatches an agent turn)        │
    ├────────────────────────────────────────────────────────┤
    │ Footer (key hints)                                     │
    └────────────────────────────────────────────────────────┘

* ``aru --tui`` routes here via ``cli.main`` / ``main.py``.
* User Enter → ``run_agent_capture_tui`` in a worker; ``TextualBusSink``
  streams into ChatPane while ToolsPane + StatusPane subscribe to the
  plugin bus for tool/turn/mode/cwd events.
* Ctrl+Q persists the session and exits cleanly.

Scope parked:

* E6c — completers (``/cmd`` / ``@file`` / ``@agent``) + paste preview
* E7  — migrating ``check_permission`` / plan-approval / ``/undo`` to ``ctx.ui``
* E8  — scrollback search + mode cycling bindings
"""

from __future__ import annotations

import asyncio
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Input

from aru.tui.widgets.chat import ChatPane
from aru.tui.widgets.completer import SlashCompleter
from aru.tui.widgets.context_pane import ContextPane
from aru.tui.widgets.loaded_pane import LoadedPane
from aru.tui.widgets.status import StatusPane
from aru.tui.widgets.thinking import ThinkingIndicator
from aru.tui.widgets.tools import ToolsPane


class AruApp(App):
    """The Aru Textual App."""

    CSS = """
    Screen {
        layout: vertical;
        padding: 0;
    }
    #body {
        height: 1fr;
        layout: horizontal;
        padding: 0;
        margin: 0;
    }
    ChatPane {
        /* Give the chat every column we can: no side padding, flex
           ratio 5:1 against the sidebar, and the sidebar itself caps
           at 36 cols so file previews have room to breathe. */
        width: 5fr;
        padding: 0;
        margin: 0;
    }
    ChatPane.-hide-sidebar {
        /* With the sidebar hidden the chat fills the whole row. */
        width: 100%;
    }
    #sidebar {
        width: 1fr;
        min-width: 24;
        max-width: 36;
        layout: vertical;
        padding: 0;
        margin: 0;
    }
    #sidebar.-hidden {
        display: none;
    }
    #input {
        /* height = 2 content rows + round border (1 top + 1 bottom) = 4.
           The extra content row gives the caret breathing room so the
           input bar doesn't feel cramped under the status line. */
        height: 4;
        border: round $primary;
        padding: 0 1;
        margin: 0;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit_app", "Quit", show=True),
        # Ctrl+C is context-sensitive: copies when there's a text
        # selection (standard TUI behaviour), otherwise interrupts the
        # current agent turn / exits. Implemented in action_ctrl_c.
        Binding("ctrl+c", "ctrl_c", "Copy/Interrupt", show=True),
        Binding("ctrl+l", "clear_chat", "Clear chat", show=True),
        Binding("ctrl+a", "cycle_mode", "Mode", show=True),
        Binding("ctrl+p", "toggle_plan", "Plan mode", show=True),
        Binding("ctrl+f", "search_chat", "Search", show=True),
        Binding("ctrl+t", "focus_tools", "Tools", show=False),
        Binding("ctrl+i", "focus_input", "Input", show=False),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar", show=True),
        Binding("ctrl+y", "copy_last", "Copy last", show=True),
        Binding("ctrl+shift+y", "copy_all", "Copy all", show=False),
        Binding("up", "history_prev", "Prev", show=False, priority=False),
        Binding("down", "history_next", "Next", show=False, priority=False),
    ]

    # Slash commands handled locally by the App (no agent round-trip).
    # Extending this map is the cheapest way to add a new local command.
    _LOCAL_SLASH = {
        "clear", "quit", "exit", "help", "plan",
        "cost", "compact", "sessions", "model", "undo",
        "skills", "agents", "commands", "mcp", "yolo",
    }

    def __init__(
        self,
        *,
        session: Any = None,
        config: Any = None,
        session_store: Any = None,
        ctx: Any = None,
        plugin_manager: Any = None,
    ) -> None:
        super().__init__()
        self.session = session
        self.config = config
        self.session_store = session_store
        self.ctx = ctx
        self.plugin_manager = plugin_manager
        self._busy = False
        # Lightweight input history (E6c minimal). Up/Down cycle through
        # prior turns so the user can re-send or tweak a message.
        self._history: list[str] = []
        self._history_cursor: int | None = None

    # ── Composition ──────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        from textual.containers import Vertical
        # AruHeader intentionally omitted — the branded logo is printed
        # into the ChatPane on mount instead (see on_mount below).
        with Horizontal(id="body"):
            yield ChatPane()
            with Vertical(id="sidebar"):
                yield ContextPane(session=self.session)
                yield LoadedPane(
                    config=self.config,
                    plugin_manager=self.plugin_manager,
                    ctx=self.ctx,
                )
        yield ThinkingIndicator()
        yield StatusPane(session=self.session)
        yield SlashCompleter()
        yield Input(
            placeholder="Type a message · / commands · @ files · Tab to accept · Enter to send",
            id="input",
        )
        yield Footer()

    def _compose_subtitle(self) -> str:
        if self.session is None:
            return ""
        sid = (
            getattr(self.session, "session_id", None)
            or getattr(self.session, "id", None)
            or "?"
        )[:8]
        model = (
            getattr(self.session, "model_ref", None)
            or getattr(self.session, "model_id", None)
            or ""
        )
        bits = [f"session {sid}"]
        if model:
            bits.append(model)
        return " · ".join(bits)

    def on_mount(self) -> None:
        self.query_one(Input).focus()
        chat = self.query_one(ChatPane)
        # Branded ASCII logo replaces the now-removed AruHeader so the
        # user sees the same "aru" welcome moment as in the REPL.
        # Wrapped in ``Align.center`` so the ASCII art sits centered on
        # the chat width instead of hugging the left margin.
        try:
            from rich.align import Align
            from aru.display import _build_logo_with_shadow, aru_logo
            chat.add_renderable(Align.center(_build_logo_with_shadow(aru_logo)))
        except Exception:
            pass
        # Tagline under the logo — includes the package version so the
        # user always knows which build they're on.
        try:
            from rich.align import Align
            from rich.text import Text as _Text
            try:
                from importlib.metadata import version as _pkg_version
                _v = _pkg_version("aru-code")
            except Exception:
                _v = ""
            tagline = _Text()
            tagline.append("A coding agent powered by OpenSource", style="italic")
            if _v:
                tagline.append(f"  v{_v}", style="dim")
            chat.add_renderable(Align.center(tagline))
            # Two blank rows so the logo+tagline aren't glued to whatever
            # lands underneath (first system line, chat message, or the
            # prompt itself on short terminals).
            from textual.widgets import Static as _Static
            spacer = _Static("")
            spacer.styles.height = 2
            chat.mount(spacer)
        except Exception:
            pass
        # Session/model info already surfaces in the sidebar ContextPane
        # and the bottom StatusPane, so no duplicate welcome line is
        # rendered under the logo — only the tagline above remains.
        self._install_bus_subscriptions()
        self._populate_completer()

    def _populate_completer(self) -> None:
        """Feed the SlashCompleter with dynamic entries from config.

        Custom commands (``.agents/commands/*.md``), custom agents
        (``.agents/agents/*.md``), skills (``skills/<name>/SKILL.md``),
        and plugin names all land here so autocomplete surfaces the full
        REPL catalogue, not just the built-in slashes.
        """
        try:
            completer = self.query_one(SlashCompleter)
        except Exception:
            return
        entries: list[tuple[str, str]] = []
        cfg = self.config
        if cfg is not None:
            for name, body in (getattr(cfg, "commands", None) or {}).items():
                desc = ""
                if isinstance(body, str):
                    desc = body.strip().split("\n", 1)[0][:80]
                entries.append((name, f"custom command  {desc}" if desc else "custom command"))
            for name, agent in (getattr(cfg, "custom_agents", None) or {}).items():
                desc = getattr(agent, "description", "") or ""
                mode = getattr(agent, "mode", "")
                label = f"custom agent ({mode})" if mode else "custom agent"
                entries.append((name, f"{label}  {desc[:60]}" if desc else label))
            for name, skill in (getattr(cfg, "skills", None) or {}).items():
                desc = getattr(skill, "description", "") or ""
                entries.append((name, f"skill  {desc[:70]}" if desc else "skill"))
        plugin_mgr = self.plugin_manager or (self.ctx and self.ctx.plugin_manager)
        if plugin_mgr is not None:
            for pname in getattr(plugin_mgr, "plugin_names", []):
                entries.append((pname, "plugin"))
        completer.set_dynamic_slashes(entries)

    def _print_startup_summary(self, chat: ChatPane) -> None:
        """Emit "Loaded …" lines for what was discovered at bootstrap."""
        cfg = self.config
        if cfg is None:
            return
        lines: list[str] = []
        if getattr(cfg, "agents_md", None):
            lines.append("• Loaded AGENTS.md")
        commands = getattr(cfg, "commands", None) or {}
        if commands:
            names = ", ".join(f"/{k}" for k in sorted(commands.keys()))
            lines.append(f"• Loaded {len(commands)} custom command(s): {names}")
        skills = getattr(cfg, "skills", None) or {}
        if skills:
            names = ", ".join(sorted(skills.keys()))
            lines.append(f"• Loaded {len(skills)} skill(s): {names}")
        custom_agents = getattr(cfg, "custom_agents", None) or {}
        if custom_agents:
            primary = [k for k, v in custom_agents.items()
                       if getattr(v, "mode", "") == "primary"]
            subagents = [k for k, v in custom_agents.items()
                         if getattr(v, "mode", "") == "subagent"]
            parts = []
            if primary:
                parts.append(", ".join(f"/{k}" for k in primary))
            if subagents:
                parts.append(f"{len(subagents)} subagent(s)")
            lines.append(
                f"• Loaded {len(custom_agents)} custom agent(s): "
                f"{', '.join(parts) if parts else ''}"
            )
        if getattr(cfg, "permissions", None):
            lines.append("• Loaded permission config")
        plugin_mgr = self.plugin_manager or (self.ctx and self.ctx.plugin_manager)
        if plugin_mgr is not None:
            plugin_count = getattr(plugin_mgr, "plugin_count", 0)
            if plugin_count:
                names = ", ".join(plugin_mgr.plugin_names)
                lines.append(f"• Loaded {plugin_count} plugin(s): {names}")
        ctx = self.ctx
        mcp_msg = getattr(ctx, "mcp_loaded_msg", None) if ctx else None
        if mcp_msg:
            lines.append(f"• {mcp_msg}")
        if lines:
            chat.add_system_message("\n".join(lines))

    # ── Input handling ───────────────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        """Keep the SlashCompleter in sync with whatever the user typed."""
        try:
            completer = self.query_one(SlashCompleter)
        except Exception:
            return
        completer.update_for(event.value or "")

    async def on_key(self, event) -> None:
        """Route Tab / Up / Down / Esc to the completer when it is open.

        Enter is intentionally NOT intercepted — it always submits the
        Input. This avoids the conflict where Enter both accepts a
        suggestion and fires ``Input.Submitted``, which produced the
        "three Enters to run /help" glitch. Tab is the only key that
        accepts the highlighted suggestion.
        """
        try:
            completer = self.query_one(SlashCompleter)
        except Exception:
            return
        if not completer.is_open():
            return
        key = event.key
        if key == "tab":
            accepted = completer.accept()
            if accepted is not None:
                inp = self.query_one(Input)
                current = inp.value or ""
                # Replace the last token with the accepted suggestion.
                if " " in current.rstrip():
                    head, _ = current.rstrip().rsplit(None, 1)
                    inp.value = f"{head} {accepted}"
                else:
                    inp.value = accepted
                inp.cursor_position = len(inp.value)
                event.stop()
                event.prevent_default()
        elif key == "up":
            completer.move_up()
            event.stop()
            event.prevent_default()
        elif key == "down":
            completer.move_down()
            event.stop()
            event.prevent_default()
        elif key == "escape":
            completer.close()
            event.stop()
            event.prevent_default()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = (event.value or "").strip()
        if not text:
            return
        event.input.value = ""
        try:
            self.query_one(SlashCompleter).close()
        except Exception:
            pass
        self._history.append(text)
        self._history_cursor = None

        # E6c: handle a small set of slash commands locally so the user
        # can navigate the TUI without a round-trip to the agent. Others
        # pass through as regular messages.
        if text.startswith("/") and self._maybe_run_local_slash(text):
            return

        if self._busy:
            self.query_one(ChatPane).add_system_message(
                "Agent is busy — wait for the current turn to finish."
            )
            return
        self._dispatch_user_turn(text)

    def _maybe_run_local_slash(self, text: str) -> bool:
        """Handle slash commands we can execute without the agent.

        Resolution order:

        1. ``_LOCAL_SLASH`` (hard-coded, TUI-specific): ``/clear``,
           ``/quit``, ``/exit``, ``/help``, ``/plan``.
        2. ``slash_bridge`` — reuse REPL's ``handle_*`` functions (e.g.
           ``/memory``, ``/worktree``, ``/subagents``, ``/plugin``,
           ``/debug``) via a captured console and mirror the output
           into the ChatPane.

        Returns True when handled (caller does NOT dispatch to agent),
        False for unknown commands — fall through to agent as text.
        """
        body_full = text[1:].strip()
        if not body_full:
            return False
        parts = body_full.split(None, 1)
        name = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

        # (1) Hard-coded local handlers.
        if name in self._LOCAL_SLASH:
            if name == "clear":
                self.action_clear_chat()
            elif name in ("quit", "exit"):
                self.action_quit_app()
            elif name == "help":
                self._render_help()
                # Also append the bridged ``_show_help`` output so users
                # see the full REPL command reference.
                self._run_bridged_slash("help", "")
            elif name == "plan":
                self.action_toggle_plan()
            elif name == "cost":
                self._slash_cost()
            elif name == "compact":
                self._slash_compact()
            elif name == "sessions":
                self._slash_sessions()
            elif name == "model":
                self._slash_model(rest)
            elif name == "undo":
                self._slash_undo()
            elif name == "skills":
                self._slash_list_skills()
            elif name == "agents":
                self._slash_list_agents()
            elif name == "commands":
                self._slash_list_commands()
            elif name == "mcp":
                self._slash_list_mcp()
            elif name == "yolo":
                self._slash_yolo()
            return True

        # (2) Bridged REPL handlers.
        from aru.tui.slash_bridge import BRIDGED_COMMANDS
        if name in BRIDGED_COMMANDS:
            self._run_bridged_slash(name, rest)
            return True

        return False

    def _run_bridged_slash(self, name: str, body: str) -> None:
        """Execute a bridged REPL handler and show its output in ChatPane."""
        from aru.tui.slash_bridge import run_bridged

        handled, text = run_bridged(name, body, self)
        if not handled:
            return
        chat = self.query_one(ChatPane)
        # Prefix with the command so the user has context.
        header = f"/{name}" + (f" {body}" if body else "")
        chat.add_system_message(f"$ {header}\n{text}" if text else f"$ {header}")

    # ── Inline slash handlers (local to TUI) ─────────────────────────

    def _push_chat(self, message: str, title: str | None = None) -> None:
        chat = self.query_one(ChatPane)
        header = f"$ /{title}" if title else ""
        if header:
            chat.add_system_message(f"{header}\n{message}")
        else:
            chat.add_system_message(message)

    def _slash_cost(self) -> None:
        session = self.session
        if session is None:
            self._push_chat("No session.", "cost")
            return
        try:
            summary = getattr(session, "cost_summary", None)
            text = summary if isinstance(summary, str) else str(summary)
        except Exception as exc:
            text = f"cost failed: {exc}"
        self._push_chat(text, "cost")

    def _slash_compact(self) -> None:
        session = self.session
        if session is None:
            self._push_chat("No session.", "compact")
            return
        self.run_worker(self._run_compact(), name="compact", group="maint")

    async def _run_compact(self) -> None:
        session = self.session
        try:
            from aru.context import compact_conversation, prune_history
            if self.ctx is not None:
                from aru.runtime import set_ctx
                set_ctx(self.ctx)
            session.history = prune_history(
                session.history, model_id=session.model_id
            )
            session.history = await compact_conversation(
                session.history,
                session.model_ref,
                getattr(session, "plan_task", None),
                model_id=session.model_id,
                invoked_skills=(
                    session.get_invoked_skills_for_agent(None)
                    if hasattr(session, "get_invoked_skills_for_agent")
                    else None
                ),
            )
            self.call_from_thread(
                self._push_chat, "Context compacted.", "compact"
            )
        except Exception as exc:
            self.call_from_thread(
                self._push_chat, f"compact failed: {exc}", "compact"
            )

    def _slash_sessions(self) -> None:
        if self.session_store is None:
            self._push_chat("No session store.", "sessions")
            return
        try:
            items = self.session_store.list_recent() \
                if hasattr(self.session_store, "list_recent") else []
        except Exception as exc:
            self._push_chat(f"sessions failed: {exc}", "sessions")
            return
        if not items:
            # Fall back to walking .aru/sessions if list_recent isn't there.
            try:
                import os
                base = getattr(self.session_store, "base_dir", ".aru/sessions")
                if os.path.isdir(base):
                    items = sorted(os.listdir(base), reverse=True)[:20]
            except Exception:
                items = []
        if not items:
            self._push_chat("No saved sessions.", "sessions")
            return
        lines = []
        for it in items:
            if isinstance(it, str):
                lines.append(f"- {it}")
            else:
                # Assume dataclass-ish with session_id + title.
                sid = getattr(it, "session_id", "?")
                title = getattr(it, "title", "")
                lines.append(f"- {sid}  {title}")
        self._push_chat("\n".join(lines), "sessions")

    def _slash_model(self, body: str) -> None:
        session = self.session
        if session is None:
            self._push_chat("No session.", "model")
            return
        body = body.strip()
        if not body:
            current = getattr(session, "model_ref", "?")
            self._push_chat(
                f"Current model: {current}\n"
                f"Usage: /model <provider/name>  (e.g. /model anthropic/claude-sonnet-4-5)",
                "model",
            )
            return
        try:
            session.model_ref = body
            if self.ctx is not None:
                self.ctx.model_id = session.model_id
            status = self.query_one(StatusPane)
            status._refresh_from_session()
            self._push_chat(f"Model switched to: {body}", "model")
        except Exception as exc:
            self._push_chat(f"model switch failed: {exc}", "model")

    def _slash_undo(self) -> None:
        # Full /undo semantics require restoring checkpoints; keep it
        # minimal for now — hint + run the bridged handler if available.
        self._push_chat(
            "Undo from the TUI is not yet implemented — use the REPL for "
            "full /undo support.",
            "undo",
        )

    def _slash_list_skills(self) -> None:
        cfg = self.config
        skills = getattr(cfg, "skills", None) or {}
        if not skills:
            self._push_chat("No skills discovered.", "skills")
            return
        lines = [f"Skills ({len(skills)}):"]
        for name, skill in sorted(skills.items()):
            desc = getattr(skill, "description", "") or ""
            lines.append(f"- {name}  {desc}")
        self._push_chat("\n".join(lines), "skills")

    def _slash_list_agents(self) -> None:
        cfg = self.config
        agents = getattr(cfg, "custom_agents", None) or {}
        if not agents:
            self._push_chat("No custom agents.", "agents")
            return
        lines = [f"Custom agents ({len(agents)}):"]
        for name, agent in sorted(agents.items()):
            mode = getattr(agent, "mode", "?")
            desc = getattr(agent, "description", "") or ""
            lines.append(f"- {name}  [{mode}]  {desc}")
        self._push_chat("\n".join(lines), "agents")

    def _slash_list_commands(self) -> None:
        cfg = self.config
        commands = getattr(cfg, "commands", None) or {}
        lines = ["Built-in:"]
        for name in sorted(self._LOCAL_SLASH):
            lines.append(f"  /{name}")
        from aru.tui.slash_bridge import BRIDGED_COMMANDS
        lines.append("")
        lines.append("Bridged from REPL:")
        for name in sorted(BRIDGED_COMMANDS.keys()):
            lines.append(f"  /{name}")
        if commands:
            lines.append("")
            lines.append(f"Custom (from .agents/commands/):")
            for name in sorted(commands.keys()):
                lines.append(f"  /{name}")
        self._push_chat("\n".join(lines), "commands")

    def _slash_list_mcp(self) -> None:
        ctx = self.ctx
        text = getattr(ctx, "mcp_catalog_text", None) if ctx else None
        if not text:
            text = "No MCP tools loaded."
        self._push_chat(text, "mcp")

    def _slash_yolo(self) -> None:
        try:
            if self.ctx is not None:
                from aru.runtime import set_ctx
                set_ctx(self.ctx)
            from aru.permissions import (
                get_permission_mode,
                set_permission_mode,
            )
            current = get_permission_mode()
            new = "default" if current == "yolo" else "yolo"
            set_permission_mode(new)
            try:
                status = self.query_one(StatusPane)
                status.mode = new
            except Exception:
                pass
            self._push_chat(f"Permission mode: {new}", "yolo")
        except Exception as exc:
            self._push_chat(f"yolo failed: {exc}", "yolo")

    def _render_help(self) -> None:
        chat = self.query_one(ChatPane)
        lines = [
            "Aru TUI — local commands & shortcuts:",
            "  /help            this message",
            "  /clear           clear chat pane",
            "  /plan            toggle plan mode",
            "  /quit  /exit     save session and exit",
            "",
            "Shortcuts:",
            "  Ctrl+Q           quit",
            "  Ctrl+L           clear chat",
            "  Ctrl+B           toggle sidebar (more chat width)",
            "  Ctrl+A           cycle permission mode",
            "  Ctrl+P           toggle plan mode",
            "  Ctrl+F           search chat",
            "  Click + drag     select text · Ctrl+C copies the selection",
            "  Ctrl+Y           copy last assistant reply (no selection needed)",
            "  Ctrl+Shift+Y     copy full transcript",
            "  Up / Down        cycle prior inputs",
            "  Tab              accept completer suggestion",
            "  Esc              close completer",
            "",
            "Anything else is sent to the agent.",
        ]
        chat.add_system_message("\n".join(lines))

    def _dispatch_user_turn(self, text: str) -> None:
        """Run the agent in a worker and stream into the chat pane."""
        chat = self.query_one(ChatPane)
        chat.add_user_message(text)
        # Expand ``@file`` mentions in the user message so the agent sees
        # actual file contents as a prefixed block. Mirrors the REPL's
        # ``_resolve_mentions`` behaviour.
        expanded = self._expand_mentions(text)
        self._busy = True
        # Flip the ThinkingIndicator on so the user sees the rotating
        # phrase while the agent reasons / streams its first tokens.
        try:
            self.query_one(ThinkingIndicator).busy = True
        except Exception:
            pass
        self.run_worker(
            self._run_turn(expanded),
            name="agent-turn",
            exclusive=True,
            group="agent",
        )

    def _expand_mentions(self, text: str) -> str:
        """Inline ``@path`` mentions by prepending file contents.

        Best-effort: tokens that look like ``@path`` where the path
        exists on disk get their contents prepended as a markdown block
        and the mention stays in the message (so the agent knows what
        was referenced). Unknown mentions are left untouched.
        """
        import re, os
        mention_re = re.compile(r"(?:^|\s)(@([^\s]+))")
        matches = list(mention_re.finditer(text))
        if not matches:
            return text
        attachments: list[str] = []
        seen: set[str] = set()
        cwd = os.getcwd()
        for _full, _mention, path in [(m.group(0), m.group(1), m.group(2))
                                       for m in matches]:
            if path in seen:
                continue
            seen.add(path)
            abs_path = path if os.path.isabs(path) else os.path.join(cwd, path)
            if not os.path.isfile(abs_path):
                continue
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    body = f.read()
            except Exception:
                continue
            if len(body) > 30_000:
                body = body[:30_000] + "\n... (truncated)"
            attachments.append(f"--- @{path} ---\n{body}")
        if not attachments:
            return text
        return "\n".join(attachments) + "\n\n" + text

    async def _run_turn(self, text: str) -> None:
        chat = self.query_one(ChatPane)
        try:
            from aru.agent_factory import create_agent_from_spec
            from aru.agents.catalog import AGENTS
            from aru.runner import run_agent_capture_tui

            if self.ctx is not None:
                from aru.runtime import set_ctx
                set_ctx(self.ctx)

            # Clear any abort flag left over from a prior Ctrl+C so the
            # new turn isn't short-circuited before it even starts.
            try:
                from aru.runtime import reset_abort
                reset_abort()
            except Exception:
                pass

            agent = await create_agent_from_spec(
                AGENTS["build"],
                session=self.session,
                model_ref=self.session.model_ref if self.session else None,
                extra_instructions=(
                    self.config.get_extra_instructions() if self.config else ""
                ),
            )
            await run_agent_capture_tui(
                agent,
                text,
                session=self.session,
                app=self,
                chat_pane=chat,
            )
            try:
                if self.session_store and self.session:
                    self.session_store.save(self.session)
            except Exception:
                pass
        except Exception as exc:  # pragma: no cover — surfaced via chat
            try:
                chat.add_system_message(
                    f"Turn failed: {type(exc).__name__}: {exc}"
                )
            except Exception:
                pass
        finally:
            self._busy = False
            # Turn off the ThinkingIndicator regardless of success / failure.
            try:
                self.query_one(ThinkingIndicator).busy = False
            except Exception:
                pass
            # Belt-and-suspenders: even if the turn.end event didn't reach
            # a subscriber for some reason, refresh directly from the
            # session state now that track_tokens has landed.
            try:
                self.query_one(StatusPane)._refresh_from_session()
            except Exception:
                pass
            try:
                self.query_one(ContextPane).refresh_from_session()
            except Exception:
                pass

    # ── Bus wiring — ToolsPane + StatusPane subscribe to plugin events ──

    def _install_bus_subscriptions(self) -> None:
        """Register bus callbacks for sidebar panes + StatusPane updates.

        The plugin manager dispatches publish() from within the App's
        own event loop (since we await it from ``run_worker`` which runs
        on the same loop). ``call_from_thread`` assumes the caller is
        off-loop, so we dispatch directly and fall back to
        ``call_from_thread`` only if a direct call fails (e.g. if a
        plugin publishes from a real worker thread).
        """
        mgr = self.plugin_manager or (self.ctx and self.ctx.plugin_manager)
        if mgr is None:
            return
        try:
            ctx_pane = self.query_one(ContextPane)
        except Exception:
            ctx_pane = None
        try:
            status = self.query_one(StatusPane)
        except Exception:
            status = None

        def _dispatch(fn, payload):
            if fn is None:
                return
            try:
                fn(payload)
            except Exception:
                try:
                    self.call_from_thread(fn, payload)
                except Exception:
                    pass

        if status is not None:
            mgr.subscribe(
                "turn.end",
                lambda p: _dispatch(status.update_from_turn, p),
            )
            mgr.subscribe(
                "permission.mode.changed",
                lambda p: _dispatch(status.update_from_mode_change, p),
            )
            mgr.subscribe(
                "cwd.changed",
                lambda p: _dispatch(status.update_from_cwd_change, p),
            )
            # Intra-turn: refresh after every internal LLM call so long
            # implementation phases show a live cost/token climb.
            mgr.subscribe(
                "metrics.updated",
                lambda p: _dispatch(status.update_from_metrics, p),
            )
        if ctx_pane is not None:
            mgr.subscribe(
                "turn.end",
                lambda p: _dispatch(ctx_pane.update_from_turn, p),
            )
            mgr.subscribe(
                "metrics.updated",
                lambda p: _dispatch(ctx_pane.update_from_metrics, p),
            )

    # ── Actions ──────────────────────────────────────────────────────

    def action_clear_chat(self) -> None:
        chat = self.query_one(ChatPane)
        for child in list(chat.children):
            try:
                child.remove()
            except Exception:
                pass
        chat.add_system_message("Chat cleared.")

    def action_cycle_mode(self) -> None:
        """Cycle permission mode (default → acceptEdits → yolo → default).

        Updates StatusPane directly — the bus publish is best-effort
        (requires ctx installed in the current task) but we don't want
        the UI to silently stay out of sync if it fails.
        """
        try:
            if self.ctx is not None:
                from aru.runtime import set_ctx
                set_ctx(self.ctx)
            from aru.permissions import cycle_permission_mode
            new_mode = cycle_permission_mode()
            # Push the mode directly into the StatusPane regardless of
            # whether the bus subscriber fired.
            try:
                self.query_one(StatusPane).mode = new_mode
            except Exception:
                pass
            self.notify(f"Permission mode: {new_mode}", severity="info")
        except Exception as exc:  # pragma: no cover
            self.notify(f"Mode cycle failed: {exc}", severity="error")

    def action_toggle_plan(self) -> None:
        """Toggle session.plan_mode flag — agent gets plan reminder next turn."""
        if self.session is None:
            return
        new_state = not bool(getattr(self.session, "plan_mode", False))
        self.session.plan_mode = new_state
        label = "ON" if new_state else "OFF"
        self.notify(f"Plan mode: {label}", severity="info")
        chat = self.query_one(ChatPane)
        chat.add_system_message(f"Plan mode {label}.")

    def action_search_chat(self) -> None:
        """Open SearchScreen; jump to the chosen message on select."""
        from aru.tui.screens import SearchScreen
        from aru.tui.widgets.chat import ChatMessageWidget

        chat = self.query_one(ChatPane)
        items: list[tuple[int, str]] = []
        for i, msg in enumerate(chat.query(ChatMessageWidget)):
            text = (msg.buffer or "").strip()
            if text:
                items.append((i, text))

        def _on_picked(idx: int | None) -> None:
            if idx is None:
                return
            msgs = list(chat.query(ChatMessageWidget))
            if 0 <= idx < len(msgs):
                try:
                    msgs[idx].scroll_visible(animate=False)
                except Exception:
                    pass

        self.push_screen(SearchScreen(items), _on_picked)

    def action_focus_tools(self) -> None:
        try:
            self.query_one(ToolsPane).focus()
        except Exception:
            pass

    def action_focus_input(self) -> None:
        try:
            self.query_one(Input).focus()
        except Exception:
            pass

    def action_toggle_sidebar(self) -> None:
        """Hide / show the right sidebar to give the chat full width."""
        try:
            sidebar = self.query_one("#sidebar")
            chat = self.query_one(ChatPane)
        except Exception:
            return
        if sidebar.has_class("-hidden"):
            sidebar.remove_class("-hidden")
            chat.remove_class("-hide-sidebar")
        else:
            sidebar.add_class("-hidden")
            chat.add_class("-hide-sidebar")

    def action_ctrl_c(self) -> None:
        """Context-sensitive Ctrl+C — matches REPL semantics.

        1. If the user has a text selection active → copy it (matches
           every other TUI: ``less``, ``htop``, shell readline, etc.).
        2. If an agent turn is running → abort the turn, keep the app
           alive, and hand the prompt back to the user. This mirrors
           the REPL where SIGINT during an agent run raises
           ``KeyboardInterrupt`` inside ``run_agent_capture`` and drops
           back to the readline prompt without exiting.
        3. Only when the prompt is already idle (no selection, no
           running turn) does Ctrl+C exit the app — same as hitting
           Ctrl+C at an empty REPL prompt.
        """
        try:
            selected = self.screen.get_selected_text() or ""
        except Exception:
            selected = ""
        if selected.strip():
            try:
                self.copy_to_clipboard(selected)
                self.notify(
                    f"Copied {len(selected)} chars to clipboard.",
                    severity="information",
                )
            except Exception as exc:
                self.notify(f"Copy failed: {exc}", severity="error")
            # Clear the selection afterwards — mirrors the shell flow
            # where the selection vanishes once copied.
            try:
                self.screen.clear_selection()
            except Exception:
                pass
            return
        if self._busy:
            # Mid-turn: interrupt the agent and return control to the
            # user. The _run_turn finally clause resets ``_busy`` and
            # the ThinkingIndicator; we just need to push a visible
            # marker into the chat and refocus the input so the user
            # can immediately type the follow-up.
            self._abort_running_turn()
            try:
                from aru.tui.widgets.chat import ChatPane
                self.query_one(ChatPane).add_system_message("Interrupted.")
            except Exception:
                pass
            try:
                self.query_one(Input).focus()
            except Exception:
                pass
            return
        # Idle prompt → exit, same as Ctrl+C at an empty REPL prompt.
        self.action_quit_app()

    def _abort_running_turn(self) -> None:
        """Signal any in-flight agent worker to cancel."""
        try:
            from aru.runtime import abort_current
            abort_current()
        except Exception:
            pass
        try:
            self.workers.cancel_all()
        except Exception:
            pass

    def action_copy_last(self) -> None:
        """Copy the last assistant reply into the system clipboard.

        The TUI owns the terminal's mouse events while running so the
        native "select + Ctrl+C" flow doesn't work. Ctrl+Y mirrors the
        vim-style yank and uses Textual's ``copy_to_clipboard`` under
        the hood (works through OSC 52 or the native backend).
        """
        from aru.tui.widgets.chat import ChatMessageWidget, ChatPane
        try:
            chat = self.query_one(ChatPane)
        except Exception:
            return
        assistants = [
            m for m in chat.query(ChatMessageWidget) if m.role == "assistant"
        ]
        if not assistants:
            self.notify("No assistant message to copy yet.", severity="warning")
            return
        text = assistants[-1].buffer or ""
        if not text.strip():
            self.notify("Last assistant message is empty.", severity="warning")
            return
        try:
            self.copy_to_clipboard(text)
            self.notify(
                f"Copied last reply ({len(text)} chars) to clipboard.",
                severity="information",
            )
        except Exception as exc:
            self.notify(f"Copy failed: {exc}", severity="error")

    def action_copy_all(self) -> None:
        """Copy the entire chat transcript to the clipboard (Ctrl+Shift+Y)."""
        from aru.tui.widgets.chat import ChatMessageWidget, ChatPane
        try:
            chat = self.query_one(ChatPane)
        except Exception:
            return
        messages = list(chat.query(ChatMessageWidget))
        if not messages:
            self.notify("Chat is empty.", severity="warning")
            return
        lines: list[str] = []
        role_prefix = {
            "user": "> user: ",
            "assistant": "assistant: ",
            "system": "[system] ",
            "tool": "  · ",
        }
        for m in messages:
            prefix = role_prefix.get(m.role, "")
            lines.append(f"{prefix}{m.buffer}")
        transcript = "\n\n".join(lines)
        try:
            self.copy_to_clipboard(transcript)
            self.notify(
                f"Copied full chat ({len(messages)} messages).",
                severity="information",
            )
        except Exception as exc:
            self.notify(f"Copy failed: {exc}", severity="error")

    def action_history_prev(self) -> None:
        """Recall the previous user input (one step older)."""
        if not self._history:
            return
        try:
            inp = self.query_one(Input)
        except Exception:
            return
        if inp.has_focus is False:
            return
        if self._history_cursor is None:
            self._history_cursor = len(self._history) - 1
        elif self._history_cursor > 0:
            self._history_cursor -= 1
        inp.value = self._history[self._history_cursor]

    def action_history_next(self) -> None:
        """Move forward in history (towards the empty line)."""
        if not self._history:
            return
        try:
            inp = self.query_one(Input)
        except Exception:
            return
        if inp.has_focus is False:
            return
        if self._history_cursor is None:
            return
        if self._history_cursor < len(self._history) - 1:
            self._history_cursor += 1
            inp.value = self._history[self._history_cursor]
        else:
            self._history_cursor = None
            inp.value = ""

    def action_quit_app(self) -> None:
        self._save_session()
        self.exit(return_code=0)

    def _save_session(self) -> None:
        try:
            if self.session is not None and self.session_store is not None:
                self.session_store.save(self.session)
        except Exception:
            pass


# ── Entry point ───────────────────────────────────────────────────────


async def run_tui(
    skip_permissions: bool = False,
    resume_id: str | None = None,
) -> None:
    """Bootstrap Aru in TUI mode and run the Textual App.

    Mirrors ``cli.run_cli`` bootstrap sequence so the TUI gets the same
    config resolution, custom tools/agents/plugins/MCP loading,
    formatter wiring, LSP, and session/worktree restoration as the REPL.
    """
    import atexit
    import logging as _logging
    import os

    from aru.cache_patch import apply_cache_patch
    from aru.config import load_config
    from aru.permissions import parse_permission_config
    from aru.plugins.manager import PluginManager
    from aru.plugins.hooks import PluginInput
    from aru.runtime import init_ctx
    from aru.session import Session, SessionStore
    from aru.tools.codebase import cleanup_processes
    from aru.tui.ui import TuiUI

    apply_cache_patch()

    ctx = init_ctx(skip_permissions=skip_permissions)

    config = load_config()
    ctx.config = config

    # LSP wiring (Tier 2 #5)
    try:
        from aru.lsp.manager import install_lsp_from_config
        install_lsp_from_config(config.lsp, root=os.getcwd())
    except Exception:
        pass

    # Populate invoke_skill's dynamic docstring with discovered skills.
    try:
        from aru.tools.skill import _update_invoke_skill_docstring
        _update_invoke_skill_docstring(config.skills)
    except Exception:
        pass

    # Register custom agents so /agent routing can resolve them.
    if config.custom_agents:
        try:
            from aru.tools.codebase import set_custom_agents
            set_custom_agents(config.custom_agents)
        except Exception:
            pass

    if config.permissions:
        ctx.perm_config = parse_permission_config(config.permissions)

    # Session resume-or-create + apply default_model from aru.json.
    store = SessionStore()
    if resume_id:
        if resume_id == "last":
            session = store.load_last() or Session()
        else:
            session = store.load(resume_id) or Session()
    else:
        session = Session()
        if config.default_model:
            session.model_ref = config.default_model
    ctx.session = session

    # Mirror _sync_model from run_cli — update RuntimeContext with the
    # session's model and resolve the small-model reference.
    try:
        from aru.providers import resolve_model_ref
        ctx.model_id = session.model_id
        small_ref = (config.model_aliases or {}).get("small") if config else None
        if not small_ref:
            provider_key, _ = resolve_model_ref(session.model_ref)
            _small_defaults = {
                "anthropic": "anthropic/claude-haiku-4-5",
                "openai": "openai/gpt-4o-mini",
                "groq": "groq/llama-3.1-8b-instant",
                "deepseek": "deepseek/deepseek-chat",
                "ollama": "ollama/llama3.1",
            }
            small_ref = _small_defaults.get(provider_key, session.model_ref)
        ctx.small_model_ref = small_ref
    except Exception:
        pass

    # Tree depth override from config.
    try:
        session._tree_max_depth = config.tree_depth
    except Exception:
        pass

    # Worktree state restoration + file-mutation invalidation.
    try:
        from aru.cli import _restore_worktree_from_session
        _restore_worktree_from_session(session)
    except Exception:
        pass
    ctx.on_file_mutation = session.invalidate_context_cache

    atexit.register(lambda: cleanup_processes(ctx.tracked_processes))

    # Checkpoint manager for /undo support.
    try:
        from aru.checkpoints import CheckpointManager
        ctx.checkpoint_manager = CheckpointManager(session.session_id)
    except Exception:
        pass

    # Custom tools discovery (synchronous — no network).
    try:
        from aru.plugins.custom_tools import (
            discover_custom_tools,
            register_custom_tools,
        )
        _disabled = getattr(config, "disabled_tools", []) or []
        _custom_tool_descs = discover_custom_tools(disabled=_disabled)
        if _custom_tool_descs:
            register_custom_tools(_custom_tool_descs)
    except Exception:
        pass

    # Plugin manager (same flow as REPL).
    plugin_mgr = PluginManager()
    ctx.plugin_manager = plugin_mgr
    try:
        _config_dict = {
            "default_model": config.default_model,
            "model_aliases": config.model_aliases,
            "permissions": config.permissions,
            "plugin_specs": config.plugin_specs,
            "disabled_tools": config.disabled_tools,
            "plan_reviewer": getattr(config, "plan_reviewer", None),
        }
        plugin_input = PluginInput(
            directory=os.getcwd(),
            config_path="aru.json" if os.path.isfile("aru.json") else "",
            model_ref=session.model_ref,
            config=_config_dict,
            session=session,
        )
        _plugin_specs = getattr(config, "plugin_specs", None) or []
        plugin_count = await plugin_mgr.load_all(
            plugin_input, plugin_specs=_plugin_specs
        )
        if plugin_count:
            try:
                plugin_tools = plugin_mgr.get_plugin_tools()
                if plugin_tools:
                    register_custom_tools(plugin_tools)
            except Exception:
                pass
    except Exception as exc:
        _logging.getLogger("aru.plugins").warning(
            "TUI plugin loading failed: %s", exc
        )

    # Install auto-formatter (Tier 3 #1).
    try:
        from aru.format.manager import install_format_from_config
        _fmt_mgr = install_format_from_config(getattr(config, "format", None))
        if _fmt_mgr is not None and _fmt_mgr.enabled():
            plugin_mgr.subscribe("file.changed", _fmt_mgr.handle_file_changed)
    except Exception:
        pass

    # Load MCP tools in the background (don't block TUI boot).
    try:
        async def _load_mcp_background():
            from aru.tools.codebase import load_mcp_tools
            await load_mcp_tools()

        asyncio.create_task(_load_mcp_background())
    except Exception:
        pass

    # Publish session.start now that plugins are loaded.
    if plugin_mgr.loaded:
        try:
            await plugin_mgr.publish("session.start", {
                "session_id": getattr(session, "id", None),
                "model_ref": session.model_ref,
                "directory": os.getcwd(),
            })
        except Exception:
            pass

    # Instantiate the App with everything wired up.
    app = AruApp(
        session=session,
        config=config,
        session_store=store,
        ctx=ctx,
        plugin_manager=plugin_mgr,
    )
    ctx.tui_app = app
    ctx.ui = TuiUI(app)

    try:
        await app.run_async()
    finally:
        ctx.tui_app = None
        ctx.ui = None
        try:
            store.save(session)
        except Exception:
            pass
