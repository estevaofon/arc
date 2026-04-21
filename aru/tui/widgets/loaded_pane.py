"""LoadedPane — 'what was loaded' summary (bottom of sidebar).

Static once the bootstrap completes: lists skills, custom agents,
custom commands, plugins, MCP servers, and AGENTS.md presence. Same
breadcrumbs the REPL prints at startup, mirrored here as a persistent
sidebar block.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Label, Static


class LoadedPane(VerticalScroll):
    """Bottom half of the sidebar: catalogue of what the session loaded."""

    DEFAULT_CSS = """
    LoadedPane {
        background: $surface;
        border-left: solid $primary;
        padding: 1 1 1 1;
        height: 1fr;
    }
    #loaded-title {
        color: $accent;
        text-style: bold;
        padding-bottom: 1;
    }
    #loaded-body {
        color: $text;
        height: auto;
    }
    """

    def __init__(
        self,
        *,
        config: Any = None,
        plugin_manager: Any = None,
        ctx: Any = None,
    ) -> None:
        super().__init__()
        self._config = config
        self._plugin_manager = plugin_manager
        self._ctx = ctx

    def compose(self) -> ComposeResult:
        yield Label("Loaded", id="loaded-title")
        yield Static("", id="loaded-body")

    def on_mount(self) -> None:
        self.refresh_from_state()

    def refresh_from_state(self) -> None:
        import os
        cfg = self._config
        mgr = self._plugin_manager or (self._ctx and self._ctx.plugin_manager)
        body = Text()

        # Current working directory — the project the session is operating
        # on. Useful at a glance so the user always knows which tree Aru
        # is rooted in (especially with worktree switching).
        cwd = None
        if self._ctx is not None:
            cwd = (
                getattr(self._ctx, "worktree_path", None)
                or getattr(self._ctx, "cwd", None)
            )
        if not cwd:
            try:
                cwd = os.getcwd()
            except Exception:
                cwd = ""
        if cwd:
            body.append("Path\n", style="bold dim")
            home = os.path.expanduser("~")
            display = cwd
            if home and display.startswith(home):
                display = "~" + display[len(home):]
            body.append(f"  {display}\n\n", style="yellow")

        def _section(title: str, count: int, items: list[str]) -> None:
            if count == 0:
                return
            body.append(f"{title} ", style="bold dim")
            body.append(f"({count})\n", style="cyan")
            for name in items[:12]:
                body.append(f"  • {name}\n", style="white")
            if count > 12:
                body.append(
                    f"  … +{count - 12} more\n", style="italic dim"
                )
            body.append("\n")

        if cfg is not None:
            skills = getattr(cfg, "skills", None) or {}
            _section("Skills", len(skills), sorted(skills.keys()))
            agents = getattr(cfg, "custom_agents", None) or {}
            _section(
                "Custom agents",
                len(agents),
                sorted(f"/{k}" for k in agents.keys()),
            )
            commands = getattr(cfg, "commands", None) or {}
            _section(
                "Custom commands",
                len(commands),
                sorted(f"/{k}" for k in commands.keys()),
            )

        if mgr is not None:
            plugin_names = list(getattr(mgr, "plugin_names", []))
            _section("Plugins", len(plugin_names), sorted(plugin_names))

        mcp_msg = ""
        if self._ctx is not None:
            mcp_msg = getattr(self._ctx, "mcp_loaded_msg", "") or ""
        if mcp_msg:
            body.append("MCP\n", style="bold dim")
            body.append(f"  {mcp_msg}\n\n", style="white")

        if cfg is not None and getattr(cfg, "agents_md", None):
            body.append("✓ AGENTS.md loaded\n", style="green")
        if cfg is not None and getattr(cfg, "permissions", None):
            body.append("✓ permission config\n", style="green")

        if len(body) == 0:
            body.append("Nothing discovered.\n", style="italic dim")
            body.append(
                "Add skills in ./.agents/skills, agents in\n"
                "./.agents/agents, or commands in\n"
                "./.agents/commands.",
                style="dim",
            )

        try:
            self.query_one("#loaded-body", Static).update(body)
        except Exception:
            pass
