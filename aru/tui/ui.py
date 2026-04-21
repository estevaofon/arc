"""TuiUI — UIAdapter backed by Textual ModalScreen (E6a + E7).

Sync-on-call: the legacy call sites (``check_permission``, plan approval,
``ask_yes_no``, ``/undo``) are synchronous. In TUI mode they are either
invoked from an ``asyncio.to_thread`` tool call or directly from the
runner coroutine wrapped with ``asyncio.to_thread``. Either way the
calling context is NOT the Textual event loop.

``TuiUI`` bridges the thread↔loop boundary with ``App.call_from_thread``
which is Textual's documented cross-thread entry point. ``push_screen_wait``
is an async coroutine that resolves with the modal's dismiss value;
wrapping it with ``call_from_thread`` schedules it on the App's loop and
blocks the caller thread until it resolves.

Contract notes:

* NEVER call these methods from the App's own event loop — it will
  deadlock. Runner-level prompts MUST be wrapped in
  ``await asyncio.to_thread(ctx.ui.ask_choice, ...)``.
* The App passed to ``TuiUI`` must be the currently-running app; if
  the app has been shut down, calls raise ``RuntimeError`` (loop closed).
"""

from __future__ import annotations

from typing import Any, Sequence

from aru.tui.screens import ChoiceModal, ConfirmModal, TextInputModal


class TuiUI:
    """UIAdapter backed by Textual ModalScreens."""

    def __init__(self, app: Any) -> None:
        self.app = app

    # ── choice ────────────────────────────────────────────────────────

    def ask_choice(
        self,
        options: Sequence[str],
        *,
        title: str | None = None,
        default: int = 0,
        cancel_value: int | None = None,
        details: Any = None,
    ) -> int | None:
        modal = ChoiceModal(
            options,
            title=title,
            default=default,
            cancel_value=cancel_value,
            details=details,
        )
        return self._run_modal(modal)

    # ── confirm ───────────────────────────────────────────────────────

    def confirm(self, prompt: str, default: bool = False) -> bool:
        modal = ConfirmModal(prompt, default=default)
        result = self._run_modal(modal)
        # ModalScreen.dismiss(False) would naturally arrive as False; None
        # only happens if the call site bypassed escape handling. Default
        # to the caller's default in that edge case.
        return default if result is None else bool(result)

    # ── text input ────────────────────────────────────────────────────

    def ask_text(
        self,
        prompt: str,
        *,
        default: str = "",
        multiline: bool = False,  # TODO: multi-line TextArea modal (deferred)
    ) -> str:
        modal = TextInputModal(prompt, default=default)
        result = self._run_modal(modal)
        return default if result is None else result

    # ── print / notify ───────────────────────────────────────────────

    def print(self, renderable: Any) -> None:  # noqa: A003
        # In TUI the print sink routes to the Chat pane once E3b lands.
        # For now, forward to the App's built-in notify as a fallback so
        # messages are visible.
        try:
            self.app.call_from_thread(self._app_print, renderable)
        except Exception:
            pass

    def _app_print(self, renderable: Any) -> None:
        # Runs on the Textual event loop — safe to touch widgets.
        try:
            chat = self.app.query_one("ChatPane")
            chat.add_system_message(str(renderable))  # type: ignore[attr-defined]
            return
        except Exception:
            pass
        # Fallback: transient toast
        try:
            self.app.notify(str(renderable))
        except Exception:
            pass

    def notify(self, message: str, severity: str = "info") -> None:
        try:
            self.app.call_from_thread(self.app.notify, message, severity=severity)
        except Exception:
            pass

    # ── internal ──────────────────────────────────────────────────────

    def _run_modal(self, modal: Any, timeout_s: float = 300.0) -> Any:
        """Push a ModalScreen and block until it is dismissed.

        Uses ``push_screen(modal, callback)`` (non-awaiting variant) and
        bridges the App loop → calling thread via ``threading.Event`` so
        we work without an active Textual worker context. Designed to be
        called from ``asyncio.to_thread`` tool threads.
        """
        import threading

        done = threading.Event()
        result: dict[str, Any] = {}

        def _on_dismiss(value: Any) -> None:
            result["value"] = value
            done.set()

        try:
            self.app.call_from_thread(self.app.push_screen, modal, _on_dismiss)
        except Exception as e:
            raise RuntimeError(
                f"TuiUI modal dispatch failed: {type(e).__name__}: {e}"
            ) from e

        if not done.wait(timeout=timeout_s):
            raise RuntimeError(
                f"TuiUI modal timed out after {timeout_s:.0f}s"
            )
        return result.get("value")
