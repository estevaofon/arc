"""Per-language LSP client manager + health state.

Responsibilities:
- Lazy-spawn one ``LspClient`` per language on first tool invocation.
- Track health per language (similar to MCP manager in Tier 1 Stage 5)
  so a crashed server fails fast instead of hanging.
- Close everything cleanly at session end.

Language detection is path-extension based. Config lives under
``aru.json`` ``lsp``::

    {
      "lsp": {
        "python":     { "command": "pylsp", "args": [] },
        "typescript": { "command": "typescript-language-server", "args": ["--stdio"] }
      }
    }
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Literal

from aru.lsp.client import LspClient, LspRequestError
from aru.lsp.protocol import path_to_uri

logger = logging.getLogger("aru.lsp")

LspHealthState = Literal["absent", "initializing", "healthy", "failed"]

# Extension -> language-id understood by most LSP servers.
_EXTENSION_LANG = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".rs": "rust",
    ".go": "go",
}


@dataclass
class LspServerHealth:
    name: str
    state: LspHealthState = "absent"
    last_error: str = ""
    last_error_at: float = 0.0


class LspManager:
    """Global LSP coordinator. Installed on ``ctx.config`` or passed explicitly."""

    def __init__(self, config_lsp: dict | None = None, root: str | None = None):
        self.config_lsp = config_lsp or {}
        self.root = root or os.getcwd()
        self.clients: dict[str, LspClient] = {}
        self.health: dict[str, LspServerHealth] = {}
        self._init_locks: dict[str, asyncio.Lock] = {}

    # -- Discovery ---------------------------------------------------

    def language_for_file(self, path: str) -> str | None:
        """Map a filename to a configured LSP language-id, or None if unsupported."""
        ext = os.path.splitext(path)[1].lower()
        lang = _EXTENSION_LANG.get(ext)
        if lang is None:
            return None
        # The config keys are arbitrary — if the user calls their entry
        # ``typescript`` we accept that; ``typescriptreact`` falls back.
        base_lang = lang.split("react")[0] if lang.endswith("react") else lang
        return base_lang if base_lang in self.config_lsp else None

    # -- Lifecycle ---------------------------------------------------

    async def get_client_for(self, path: str) -> LspClient | None:
        lang = self.language_for_file(path)
        if lang is None:
            return None
        lock = self._init_locks.setdefault(lang, asyncio.Lock())
        async with lock:
            health = self.health.setdefault(lang, LspServerHealth(name=lang))
            if lang in self.clients and health.state == "healthy":
                return self.clients[lang]
            if health.state == "failed":
                return None

            cfg = self.config_lsp.get(lang) or {}
            command = cfg.get("command")
            if not command:
                return None
            args = cfg.get("args") or []
            env = cfg.get("env") or None

            health.state = "initializing"
            client = LspClient(
                command=[command, *args],
                root_uri=path_to_uri(self.root),
                cwd=self.root,
                env=env,
            )
            try:
                await client.start()
            except Exception as exc:
                health.state = "failed"
                health.last_error = str(exc)
                health.last_error_at = time.time()
                logger.warning("LSP %s failed to start: %s", lang, exc)
                return None
            self.clients[lang] = client
            health.state = "healthy"
            return client

    async def shutdown_all(self) -> None:
        for client in list(self.clients.values()):
            try:
                await client.shutdown()
            except Exception:
                pass
        self.clients.clear()
        for h in self.health.values():
            h.state = "absent"


# ── Global singleton ────────────────────────────────────────────────

_manager: LspManager | None = None


def get_lsp_manager() -> LspManager | None:
    return _manager


def set_lsp_manager(mgr: LspManager | None) -> None:
    global _manager
    _manager = mgr


def install_lsp_from_config(config_lsp: dict | None, root: str) -> LspManager | None:
    """Instantiate and register the global manager if the config has any language."""
    if not config_lsp:
        set_lsp_manager(None)
        return None
    mgr = LspManager(config_lsp=config_lsp, root=root)
    set_lsp_manager(mgr)
    return mgr
