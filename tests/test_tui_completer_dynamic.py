"""Tests for dynamic completer entries (custom commands/agents/skills/plugins)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

pytest.importorskip("textual")


@pytest.mark.asyncio
async def test_completer_suggests_custom_agent():
    @dataclass
    class _Agent:
        mode: str = "subagent"
        description: str = "Code review specialist"

    @dataclass
    class _Cfg:
        commands: dict
        custom_agents: dict
        skills: dict

    cfg = _Cfg(
        commands={},
        custom_agents={"code-reviewer": _Agent()},
        skills={},
    )

    from aru.tui.app import AruApp
    from aru.tui.widgets.completer import SlashCompleter
    from textual.widgets import OptionList

    app = AruApp(config=cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        completer = app.query_one(SlashCompleter)
        completer.update_for("/code")
        await pilot.pause()
        opts = completer.query_one(OptionList)
        ids = [opts.get_option_at_index(i).id for i in range(opts.option_count)]
        assert "code-reviewer" in ids


@pytest.mark.asyncio
async def test_completer_suggests_skill():
    @dataclass
    class _Skill:
        description: str = "Systematic debugging playbook"

    @dataclass
    class _Cfg:
        commands: dict
        custom_agents: dict
        skills: dict

    cfg = _Cfg(
        commands={},
        custom_agents={},
        skills={"systematic-debugging": _Skill()},
    )

    from aru.tui.app import AruApp
    from aru.tui.widgets.completer import SlashCompleter
    from textual.widgets import OptionList

    app = AruApp(config=cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        completer = app.query_one(SlashCompleter)
        completer.update_for("/sys")
        await pilot.pause()
        opts = completer.query_one(OptionList)
        ids = [opts.get_option_at_index(i).id for i in range(opts.option_count)]
        assert "systematic-debugging" in ids


@pytest.mark.asyncio
async def test_completer_suggests_custom_command():
    @dataclass
    class _Cfg:
        commands: dict
        custom_agents: dict
        skills: dict

    cfg = _Cfg(
        commands={"hello": "# Hello World\n\nSay hi."},
        custom_agents={},
        skills={},
    )

    from aru.tui.app import AruApp
    from aru.tui.widgets.completer import SlashCompleter
    from textual.widgets import OptionList

    app = AruApp(config=cfg)
    async with app.run_test() as pilot:
        await pilot.pause()
        completer = app.query_one(SlashCompleter)
        completer.update_for("/hel")
        await pilot.pause()
        opts = completer.query_one(OptionList)
        ids = [opts.get_option_at_index(i).id for i in range(opts.option_count)]
        # Custom command should appear alongside built-in /help.
        assert "help" in ids
        assert "hello" in ids


@pytest.mark.asyncio
async def test_completer_suggests_plugin_name():
    @dataclass
    class _Cfg:
        commands: dict
        custom_agents: dict
        skills: dict

    class _FakePluginMgr:
        plugin_names = ["code-formatter", "my-plugin"]

        def subscribe(self, _event, _cb):
            pass

        def subscribe_all(self, _cb):
            pass

    from aru.tui.app import AruApp
    from aru.tui.widgets.completer import SlashCompleter
    from textual.widgets import OptionList

    cfg = _Cfg(commands={}, custom_agents={}, skills={})
    app = AruApp(config=cfg, plugin_manager=_FakePluginMgr())
    async with app.run_test() as pilot:
        await pilot.pause()
        completer = app.query_one(SlashCompleter)
        completer.update_for("/my-pl")
        await pilot.pause()
        opts = completer.query_one(OptionList)
        ids = [opts.get_option_at_index(i).id for i in range(opts.option_count)]
        assert "my-plugin" in ids


@pytest.mark.asyncio
async def test_stream_state_carries_run_output():
    """Regression: run_output must be exposed on StreamState so token
    accounting lands. The previous refactor dropped it on the floor."""
    from aru.streaming import StreamState

    s = StreamState()
    assert s.run_output is None
    s.run_output = object()
    assert s.run_output is not None
