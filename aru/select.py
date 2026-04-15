"""Interactive arrow-key menu — Claude Code-style option selection.

Provides `select_option`, a small prompt_toolkit `Application` that shows
a list of options with a highlighted cursor and accepts up/down navigation
+ Enter to confirm. Number keys (1..9) remain as a power-user shortcut so
existing muscle memory still works. Esc / Ctrl+C return the configured
cancel value.

The menu is intentionally small (no full-screen, no box, no shadow) so it
blends into the Rich-rendered permission panel that precedes it. It pauses
any active `ctx.live` the caller passes in, renders while the Live is
stopped, and lets the caller restart Live afterwards.

Non-interactive fallback: if stdin is not a TTY, the function returns
`default` immediately without attempting to render — lets CI, piped input,
and one-shot invocations behave as "auto-accept the default".
"""

from __future__ import annotations

import asyncio
import sys
from typing import Sequence


def select_option(
    options: Sequence[str],
    title: str | None = None,
    default: int = 0,
    cancel_value: int | None = None,
) -> int | None:
    """Show an interactive arrow-key menu and return the selected index.

    Args:
        options: Display labels, one per line. Rendered verbatim (no
            markup parsing) so callers should pass already-formatted text
            or plain strings. Indices 1..N are also bound as direct
            shortcuts.
        title: Optional header line shown above the list.
        default: Initial cursor position (0-based). Also returned when
            stdin is not a TTY.
        cancel_value: Returned when the user presses Esc or Ctrl+C.
            `None` (default) matches "user bailed out" semantics used by
            the permission prompt and plan approval.

    Returns:
        The 0-based index of the chosen option, or `cancel_value` if the
        user canceled.
    """
    if not options:
        raise ValueError("select_option requires at least one option")

    # Clamp default so a stale caller doesn't crash the menu.
    default = max(0, min(default, len(options) - 1))

    # Non-interactive: nothing to render. Return the default immediately.
    if not sys.stdin.isatty():
        return default

    # Lazy import — prompt_toolkit is a hot dependency (pulls terminal
    # state) and we don't want to pay the cost on every import of this
    # module when callers aren't going to run the menu.
    from prompt_toolkit import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.styles import Style

    cursor = [default]

    def _render() -> FormattedText:
        fragments: list[tuple[str, str]] = []
        if title:
            fragments.append(("class:title", f"{title}\n"))
        for i, label in enumerate(options):
            prefix = "❯ " if i == cursor[0] else "  "
            style = "class:selected" if i == cursor[0] else "class:option"
            fragments.append((style, f"{prefix}{label}\n"))
        return FormattedText(fragments)

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("c-p")
    @kb.add("k")
    def _(event):
        cursor[0] = (cursor[0] - 1) % len(options)

    @kb.add("down")
    @kb.add("c-n")
    @kb.add("j")
    def _(event):
        cursor[0] = (cursor[0] + 1) % len(options)

    @kb.add("enter")
    def _(event):
        event.app.exit(result=cursor[0])

    @kb.add("escape", eager=True)
    @kb.add("c-c")
    def _(event):
        event.app.exit(result=cancel_value)

    # Number-key shortcuts 1..9 — instant selection + confirm.
    for i in range(min(len(options), 9)):
        key = str(i + 1)

        def _handler(event, idx=i):
            event.app.exit(result=idx)

        kb.add(key)(_handler)

    style = Style.from_dict(
        {
            "title": "bold",
            "selected": "bold cyan",
            "option": "",
        }
    )

    app = Application(
        layout=Layout(Window(FormattedTextControl(_render), always_hide_cursor=True)),
        key_bindings=kb,
        style=style,
        full_screen=False,
        mouse_support=False,
        erase_when_done=False,
    )

    try:
        return _run_app_sync(app)
    except (EOFError, KeyboardInterrupt):
        return cancel_value


def _run_app_sync(app):
    """Run a prompt_toolkit Application from any context (sync OR async).

    `Application.run()` internally calls `asyncio.run()`, which raises
    `RuntimeError: asyncio.run() cannot be called from a running event loop`
    when the caller is inside a coroutine (e.g. Aru's async tool wrappers
    call `select_option` from inside `exit_plan_mode` / the permission
    check, which runs under the Agno agent's event loop).

    To keep `select_option` synchronous for simple sync call sites while
    also supporting async ones, we detect whether a loop is currently
    running:

    - **No running loop** → call `app.run()` directly. This is the fast
      path for unit tests, CLI startup before the agent loop begins, and
      any future sync callers.
    - **Loop running** → offload `app.run()` to a worker thread via a
      single-shot `ThreadPoolExecutor`. The worker thread has no event
      loop of its own, so `asyncio.run()` inside `app.run()` succeeds
      there. The main thread blocks on `future.result()`, which is the
      correct semantics: the user must answer before the agent can
      continue, so every other async task should stop anyway.

    Blocking the main event-loop thread is usually an anti-pattern, but
    here it's exactly what we want. The Aru runner already halts the
    Live display and no other tools run in parallel with a permission
    prompt (serialized via `ctx.permission_lock`), so there is nothing
    useful for other tasks to do while we wait.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No loop running — safe to use the sync entry point directly.
        return app.run()

    # Running inside an event loop. Run the Application in a worker thread
    # that has its own fresh (loop-less) thread state, where
    # `Application.run()` → `asyncio.run()` can create a loop without
    # conflict.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(app.run)
        return future.result()
