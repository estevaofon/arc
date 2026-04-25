"""Layer 12 — mouse-tracking recovery: off-then-on shake + keypress trigger.

Background: ``aru/tui/widgets/chat.py`` post-mortem under "self-heal didn't
recover the wheel" (2026-04-25). Layers 9/10 emitted only the four ``?...h``
enable sequences; if ConPTY's enable-cache or the driver's gate suppressed
the write, no recovery happened. Layer 12 emits a forced ``?...l → ?...h``
state transition via ``driver.write`` and adds a per-keypress trigger so the
user gets sub-second recovery instead of waiting for the periodic tick.

These tests pin down the observable contracts:
* the eight DEC private-mode sequences are emitted in disable→enable order;
* the keypress trigger calls into ``_reenable_mouse_tracking`` debounced by
  ``_KEYPRESS_REARM_DEBOUNCE``.
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


@pytest.mark.asyncio
async def test_reenable_mouse_tracking_emits_off_then_on_shake():
    """Layer 12: ``?...l`` (4) is emitted *before* ``?...h`` (4), then flush.

    The off→on shake forces ConPTY's enable-cache through a state
    transition, defeating the case where its cache claims ``?1000`` is
    already ``h`` and suppresses the propagated write. Order matters —
    if the on sequences came first the cache could no-op them.
    """
    from aru.tui.app import AruApp

    app = AruApp()
    rec = _RecordingDriver()
    # ``_driver`` is a private slot of ``App`` — assigning directly
    # short-circuits the application-mode startup that would normally
    # set it. The recovery method only reads ``self._driver``.
    app._driver = rec

    app._reenable_mouse_tracking()

    expected_off = [
        "\x1b[?1000l",
        "\x1b[?1003l",
        "\x1b[?1015l",
        "\x1b[?1006l",
    ]
    expected_on = [
        "\x1b[?1000h",
        "\x1b[?1003h",
        "\x1b[?1015h",
        "\x1b[?1006h",
    ]
    assert rec.writes == expected_off + expected_on
    # One flush at the end — the ``WriterThread`` bufferises everything
    # before that into a single terminal emit.
    assert rec.flushes == 1


@pytest.mark.asyncio
async def test_reenable_mouse_tracking_no_driver_is_noop():
    """If the driver is ``None`` (headless / pre-mount) the call is a quiet no-op."""
    from aru.tui.app import AruApp

    app = AruApp()
    app._driver = None
    # Must not raise.
    app._reenable_mouse_tracking()


@pytest.mark.asyncio
async def test_keypress_rearm_is_debounced(monkeypatch):
    """Layer 12 keypress trigger respects ``_KEYPRESS_REARM_DEBOUNCE``.

    Two keystrokes within the debounce window should produce exactly one
    ``_reenable_mouse_tracking`` invocation; a third keystroke after the
    window elapses should produce a second.
    """
    from aru.tui import app as app_mod
    from aru.tui.app import AruApp

    app = AruApp()
    rec = _RecordingDriver()
    app._driver = rec

    fake_now = [100.0]

    def fake_monotonic() -> float:
        return fake_now[0]

    monkeypatch.setattr(app_mod.time, "monotonic", fake_monotonic)

    # 1st keystroke at t=100 — fires.
    app._maybe_rearm_mouse_on_keypress()
    # 2nd keystroke 100 ms later — within 500 ms debounce → suppressed.
    fake_now[0] += 0.1
    app._maybe_rearm_mouse_on_keypress()
    # 3rd keystroke 600 ms after 1st (i.e. 500 ms after debounce window
    # opened) — fires.
    fake_now[0] += 0.5
    app._maybe_rearm_mouse_on_keypress()

    # Each fired call emits 8 sequences (4 off + 4 on). Two fires = 16.
    assert len(rec.writes) == 16
    assert rec.flushes == 2
