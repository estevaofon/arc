"""LoadedPane shows the current working directory (user request)."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_loaded_pane_shows_cwd_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    from aru.tui.app import AruApp
    from aru.tui.widgets.loaded_pane import LoadedPane

    app = AruApp()
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        pane = app.query_one(LoadedPane)
        pane.refresh_from_state()
        await pilot.pause()
        import re, html
        svg = app.export_screenshot()
        text = "".join(
            html.unescape(m) for m in
            re.findall(r"<text[^>]*>([^<]*)</text>", svg, re.DOTALL)
        ).replace("\xa0", " ")
    assert "Path" in text
    # Path is home-prefixed AND may be word-wrapped across multiple
    # sidebar rows, so assert on the first 10 chars of the basename
    # which survive the wrap.
    basename = os.path.basename(str(tmp_path))
    stripped = text.replace("│", "").replace("\n", "")
    assert basename[:10] in stripped


@pytest.mark.asyncio
async def test_loaded_pane_prefers_worktree_path():
    """When ctx.worktree_path is set, LoadedPane should show that instead."""
    from aru.tui.app import AruApp
    from aru.tui.widgets.loaded_pane import LoadedPane

    class _Ctx:
        worktree_path = "/tmp/my-worktree-feature"
        cwd = "/home/user/other"
        plugin_manager = None
        mcp_loaded_msg = ""

    app = AruApp(ctx=_Ctx())
    async with app.run_test(size=(140, 50)) as pilot:
        await pilot.pause()
        pane = app.query_one(LoadedPane)
        pane.refresh_from_state()
        await pilot.pause()
        import re, html
        svg = app.export_screenshot()
        text = "".join(
            html.unescape(m) for m in
            re.findall(r"<text[^>]*>([^<]*)</text>", svg, re.DOTALL)
        ).replace("\xa0", " ")
    # Worktree path wins over generic cwd. Path wraps char-by-char at
    # narrow sidebar widths so we check a distinctive substring.
    assert "worktree" in text
    assert "other" not in text
