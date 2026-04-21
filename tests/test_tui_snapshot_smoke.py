"""Lightweight visual smoke tests for the TUI (E9).

Rather than pulling in pytest-textual-snapshot (version-sensitive against
Textual 8.x), we take SVG screenshots and assert against stable textual
markers — presence of the header/branding, expected labels, etc.

This guards against accidental layout regressions without pinning an
entire SVG payload which would churn on every Textual release.
"""

from __future__ import annotations

import html
import re

import pytest

pytest.importorskip("textual")


_TEXT_SPAN = re.compile(r"<text[^>]*>([^<]*)</text>", flags=re.DOTALL)


def _svg_to_text(svg: str) -> str:
    """Extract visible text content from a Textual-generated SVG.

    Textual SVG screenshots render each cell as a separate ``<text>`` span
    so literal substring matches against the raw SVG fail. Joining the
    spans together — best-effort — gives us the rendered content as a
    single string we can assert on. NBSP (``\\xa0``) → ascii space so
    plain-English substring checks work.
    """
    parts = [html.unescape(m) for m in _TEXT_SPAN.findall(svg)]
    joined = "".join(parts)
    return joined.replace("\xa0", " ")


async def _render_to_text(app, pilot) -> str:
    await pilot.pause(0.15)
    return _svg_to_text(app.export_screenshot())


@pytest.mark.asyncio
async def test_snapshot_empty_app_contains_brand_and_prompt():
    from aru.tui.app import AruApp

    app = AruApp()
    async with app.run_test(size=(100, 30)) as pilot:
        svg = await _render_to_text(app, pilot)
    # Branded ASCII logo replaced the old AruHeader; at least one of its
    # distinctive block chars should land in the viewport.
    assert any(ch in svg for ch in ("█", "▖", "▗", "▘", "▝")), svg[:300]
    # Tagline line includes the package version marker "vX.Y.Z".
    import re as _re
    assert _re.search(r"v\d+\.\d+\.\d+", svg) is not None
    # Input placeholder
    assert "Type a message" in svg


@pytest.mark.asyncio
async def test_snapshot_after_chat_messages_contains_content():
    from aru.tui.app import AruApp
    from aru.tui.widgets.chat import ChatPane

    app = AruApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        chat = app.query_one(ChatPane)
        chat.add_user_message("hello world")
        chat.start_assistant_message()
        chat.append_assistant_delta("reply!")
        await pilot.pause(0.2)
        chat.finalize_assistant_message()
        svg = await _render_to_text(app, pilot)
    assert "hello world" in svg
    assert "reply" in svg


@pytest.mark.asyncio
async def test_snapshot_sidebar_shows_context_and_loaded():
    """New split sidebar: Context Window + Loaded blocks visible by default."""
    from aru.tui.app import AruApp

    app = AruApp()
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        svg = await _render_to_text(app, pilot)
    assert "Context Window" in svg
    assert "Loaded" in svg


@pytest.mark.asyncio
async def test_snapshot_status_pane_renders_in_viewport():
    """Once removed from a competing dock, StatusPane is visible."""
    from aru.session import Session
    from aru.tui.app import AruApp
    from aru.tui.widgets.status import StatusPane

    session = Session()
    session.total_input_tokens = 1500
    session.total_output_tokens = 400
    app = AruApp(session=session)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        status = app.query_one(StatusPane)
        status._refresh_from_session()
        status.update_from_mode_change({"new_mode": "acceptEdits"})
        await pilot.pause()
        rendered = await _render_to_text(app, pilot)
    # Status line shows the mode badge after the change; tokens moved
    # to the sidebar ContextPane so we no longer assert on them here.
    assert "accept" in rendered.lower()


@pytest.mark.asyncio
async def test_status_pane_reactives_update_correctly():
    """StatusPane reactive fields flip when session changes + mode flips."""
    from aru.session import Session
    from aru.tui.app import AruApp
    from aru.tui.widgets.status import StatusPane

    session = Session()
    session.total_input_tokens = 1500
    session.total_output_tokens = 400
    app = AruApp(session=session)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        status = app.query_one(StatusPane)
        status._refresh_from_session()
        status.update_from_mode_change({"new_mode": "acceptEdits"})
        await pilot.pause()
    assert status.tokens_in == 1500
    assert status.tokens_out == 400
    assert status.mode == "acceptEdits"


@pytest.mark.asyncio
async def test_snapshot_choice_modal_renders_options():
    from aru.tui.app import AruApp
    from aru.tui.screens import ChoiceModal

    app = AruApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(ChoiceModal(["Allow", "Deny"], title="Permission"))
        await pilot.pause(0.2)
        svg = await _render_to_text(app, pilot)
    assert "Allow" in svg
    assert "Deny" in svg
    assert "Permission" in svg


@pytest.mark.asyncio
async def test_snapshot_search_modal_renders():
    from aru.tui.app import AruApp
    from aru.tui.screens import SearchScreen

    app = AruApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        app.push_screen(SearchScreen([(0, "hello world"), (1, "refactor api")]))
        await pilot.pause(0.2)
        svg = await _render_to_text(app, pilot)
    assert "Search chat" in svg
