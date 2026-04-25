"""Layer 13 — user-invoked terminal recovery binding (Ctrl+R).

Background: ``aru/tui/widgets/chat.py`` post-mortem under "no self-heal
of Layers 9/10/12 actually recovers" (2026-04-25). User report on
``fix/scroll-analysis3``: after a Windows display sleep/wake the wheel
dies and *no* existing recovery brings it back, including the
``_run_turn`` finally-clause call to ``_reenable_mouse_tracking``.

Diagnosis from that session:

1. Layer 12's keypress trigger never fires during normal typing —
   ``Input._on_key`` calls ``event.stop()`` on printable keys
   (``textual/widgets/_input.py:736-737``), so ``App.on_key`` is
   never reached and ``_maybe_rearm_mouse_on_keypress`` stays cold.

2. Even when ``_reenable_mouse_tracking`` does fire (Layer 9 turn
   boundary), re-emitting mouse-only DEC private modes is not enough
   for this particular failure mode. The full set the driver enables
   at boot includes focus-events (?1004) and bracketed paste (?2004),
   any of which may have dropped together with mouse on display wake.

Layer 13 adds ``AruApp.action_recover_terminal`` bound to ``Ctrl+R``
with ``priority=True`` — a user-invoked recovery that guarantees the
strong shake fires (bindings bypass widget-level key consumption).

These tests pin the observable contracts:
* the binding is registered to the action;
* the action emits the full DEC mode set in disable→enable order
  with one final flush;
* the action is a quiet no-op when the driver is unavailable
  (headless / pre-mount).

The Windows-only ``set_console_mode`` step (re-asserting
``ENABLE_VIRTUAL_TERMINAL_INPUT`` on stdin) is exercised at runtime
only — it is wrapped in try/except and gated on ``sys.platform``, so
unit-testing it would require ctypes mocking that adds little value
over the integration test (manual TUI run on Windows).
"""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


class _RecordingDriver:
    """Driver stub that records every ``write`` call."""

    def __init__(self) -> None:
        self.writes: list[str] = []
        self.flushes: int = 0

    def write(self, data: str) -> None:
        self.writes.append(data)

    def flush(self) -> None:
        self.flushes += 1


def test_recover_terminal_binding_present():
    """Layer 13: ``Ctrl+R`` is bound and dispatches ``recover_terminal``.

    The binding must use ``priority=True`` so the action fires even
    when a focused widget would otherwise consume the key — that is
    the whole point vs Layer 12's broken ``on_key`` path.
    """
    from aru.tui.app import AruApp

    matches = [b for b in AruApp.BINDINGS if getattr(b, "key", None) == "ctrl+r"]
    assert matches, "ctrl+r must be in AruApp.BINDINGS"
    binding = matches[0]
    assert binding.action == "recover_terminal"
    assert binding.priority is True


@pytest.mark.asyncio
async def test_action_recover_terminal_emits_full_mode_shake():
    """Layer 13: full DEC mode set, off→on order, single flush.

    The order matters — ``?...l`` first defeats ConPTY's enable-cache
    by forcing a state transition before the ``?...h`` re-emit (same
    rationale as Layer 12 but on a wider set of modes). One flush at
    the end so the ``WriterThread`` ships everything in one terminal
    emit.
    """
    from aru.tui.app import AruApp

    app = AruApp()
    rec = _RecordingDriver()
    app._driver = rec

    app.action_recover_terminal()

    expected_off = [
        "\x1b[?1000l",
        "\x1b[?1003l",
        "\x1b[?1015l",
        "\x1b[?1006l",
        "\x1b[?1004l",
        "\x1b[?2004l",
    ]
    expected_on = [
        "\x1b[?1000h",
        "\x1b[?1003h",
        "\x1b[?1015h",
        "\x1b[?1006h",
        "\x1b[?1004h",
        "\x1b[?2004h",
    ]
    assert rec.writes == expected_off + expected_on
    assert rec.flushes == 1


@pytest.mark.asyncio
async def test_action_recover_terminal_no_driver_is_noop():
    """Headless / pre-mount: action must not raise when driver is None."""
    from aru.tui.app import AruApp

    app = AruApp()
    app._driver = None
    # Must not raise — ChatPane query, refresh(), and console-mode
    # branch all wrapped in try/except for exactly this case.
    app.action_recover_terminal()


@pytest.mark.asyncio
async def test_action_recover_terminal_full_mode_set_is_superset_of_layer12():
    """Layer 13's mode set must cover everything Layer 12 covers, plus more.

    If a future refactor splits the constants and accidentally drops
    a Layer 12 mode from Layer 13, this test catches it. Layer 13 is
    the strict superset — same four mouse modes, plus focus-events
    and bracketed-paste.
    """
    from aru.tui.app import AruApp

    layer12_off = set(AruApp._MOUSE_DISABLE_SEQS)
    layer12_on = set(AruApp._MOUSE_ENABLE_SEQS)
    layer13_off = set(AruApp._FULL_MODE_DISABLE_SEQS)
    layer13_on = set(AruApp._FULL_MODE_ENABLE_SEQS)

    assert layer12_off.issubset(layer13_off)
    assert layer12_on.issubset(layer13_on)
    # Extra modes Layer 13 adds (focus events ?1004 and paste ?2004).
    assert "\x1b[?1004h" in layer13_on
    assert "\x1b[?2004h" in layer13_on
    assert "\x1b[?1004l" in layer13_off
    assert "\x1b[?2004l" in layer13_off
