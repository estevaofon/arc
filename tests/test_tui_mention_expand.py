"""Tests for @file mention expansion in TUI dispatch."""

from __future__ import annotations

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_expand_mentions_inlines_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.md").write_text("hello from notes")

    from aru.tui.app import AruApp
    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        expanded = app._expand_mentions("check @notes.md please")
    assert "hello from notes" in expanded
    assert "@notes.md" in expanded  # original mention kept


@pytest.mark.asyncio
async def test_expand_mentions_no_op_on_unknown_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from aru.tui.app import AruApp
    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        expanded = app._expand_mentions("what about @missing.md")
    # No file matched → message unchanged.
    assert expanded == "what about @missing.md"


@pytest.mark.asyncio
async def test_expand_mentions_truncates_large_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    big = "x" * 40_000
    (tmp_path / "huge.txt").write_text(big)
    from aru.tui.app import AruApp
    app = AruApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        expanded = app._expand_mentions("@huge.txt")
    assert "(truncated)" in expanded
