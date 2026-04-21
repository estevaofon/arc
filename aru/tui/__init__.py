"""Aru TUI (Textual) entry points.

Opt-in via ``aru --tui``. The REPL (default ``aru``) remains the
canonical mode; the TUI reuses the same bootstrap, config, session store,
plugin manager, and agent catalog — only presentation differs.

Public API:

* ``run_tui(skip_permissions=False, resume_id=None)`` — async entry point
  called from ``aru.cli.main``.
"""

from aru.tui.app import AruApp, run_tui

__all__ = ["AruApp", "run_tui"]
