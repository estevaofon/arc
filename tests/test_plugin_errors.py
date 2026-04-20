"""Stage 2 regression: plugin error observability.

Covers:
- Subscriber exceptions captured in the manager's ring buffer (not silenced)
- logger.error is emitted so the configured stderr handler sees it
- Ring buffer respects maxlen (capacity rotation)
- `/debug plugin-errors` surfaces captured errors
- Existing publish semantics (synchronous AND async subscribers) keep working
"""

import logging
import pytest

from aru.plugins.manager import PluginManager, _ERROR_LOG_CAPACITY


@pytest.mark.asyncio
async def test_sync_subscriber_error_captured():
    mgr = PluginManager()
    mgr._loaded = True

    def bad(payload):
        raise RuntimeError("deliberate test failure")

    mgr.subscribe("session.start", bad)
    await mgr.publish("session.start", {"session_id": "X"})

    errors = mgr.recent_errors()
    assert len(errors) == 1
    e = errors[0]
    assert e["category"] == "subscriber"
    assert e["event"] == "session.start"
    assert e["error_type"] == "RuntimeError"
    assert "deliberate test failure" in e["error"]
    assert "bad" in e["source"]
    assert e["traceback"]  # non-empty


@pytest.mark.asyncio
async def test_async_subscriber_error_captured():
    mgr = PluginManager()
    mgr._loaded = True

    async def bad_async(payload):
        raise ValueError("async fail")

    mgr.subscribe("msg", bad_async)
    await mgr.publish("msg", {})

    errors = mgr.recent_errors()
    assert len(errors) == 1
    assert errors[0]["error_type"] == "ValueError"


@pytest.mark.asyncio
async def test_wildcard_error_categorised_separately():
    mgr = PluginManager()
    mgr._loaded = True

    def bad_wildcard(payload):
        raise RuntimeError("wildcard boom")

    mgr.subscribe_all(bad_wildcard)
    await mgr.publish("whatever", {})

    errors = mgr.recent_errors()
    assert len(errors) == 1
    assert errors[0]["category"] == "wildcard"


@pytest.mark.asyncio
async def test_one_bad_subscriber_does_not_block_others():
    mgr = PluginManager()
    mgr._loaded = True

    calls: list[str] = []

    def bad(payload):
        raise RuntimeError("boom")

    def good(payload):
        calls.append("good")

    mgr.subscribe("ev", bad)
    mgr.subscribe("ev", good)
    await mgr.publish("ev", {})

    assert calls == ["good"]
    assert len(mgr.recent_errors()) == 1


@pytest.mark.asyncio
async def test_logger_emits_record(caplog):
    mgr = PluginManager()
    mgr._loaded = True

    def bad(payload):
        raise RuntimeError("visible in log")

    mgr.subscribe("ev", bad)
    with caplog.at_level(logging.ERROR, logger="aru.plugins"):
        await mgr.publish("ev", {})

    records = [r for r in caplog.records if r.name == "aru.plugins"]
    assert records, "expected at least one record on aru.plugins logger"
    assert any("visible in log" in r.getMessage() or "RuntimeError" in r.getMessage()
               for r in records)


@pytest.mark.asyncio
async def test_error_log_capacity_rotates():
    mgr = PluginManager()
    mgr._loaded = True

    def bad(payload):
        raise RuntimeError("x")

    mgr.subscribe("ev", bad)
    for _ in range(_ERROR_LOG_CAPACITY + 20):
        await mgr.publish("ev", {})

    assert len(mgr.recent_errors()) == _ERROR_LOG_CAPACITY


@pytest.mark.asyncio
async def test_successful_subscriber_adds_nothing_to_log():
    mgr = PluginManager()
    mgr._loaded = True

    def ok(payload):
        pass

    mgr.subscribe("ev", ok)
    await mgr.publish("ev", {})
    assert mgr.recent_errors() == []


def test_configure_plugin_logger_is_idempotent():
    """Calling _configure_plugin_logger twice must not stack handlers."""
    from aru.cli import _configure_plugin_logger
    lg = logging.getLogger("aru.plugins")
    # Clear any state from autouse fixtures
    lg.handlers = [h for h in lg.handlers
                   if not getattr(h, "_aru_test_marker", False)]
    lg._aru_handler_attached = False  # type: ignore[attr-defined]

    _configure_plugin_logger()
    n1 = len(lg.handlers)
    _configure_plugin_logger()
    n2 = len(lg.handlers)
    assert n1 == n2


def test_debug_command_renders_errors(capsys, monkeypatch):
    """`handle_debug_command('plugin-errors')` prints a table with captured errors."""
    import asyncio
    from aru.commands import handle_debug_command
    from aru.runtime import get_ctx

    ctx = get_ctx()
    mgr = PluginManager()
    mgr._loaded = True

    def bad(payload):
        raise RuntimeError("pretty-print me")

    mgr.subscribe("session.start", bad)
    asyncio.get_event_loop().run_until_complete(
        mgr.publish("session.start", {"session_id": "T"})
    )
    ctx.plugin_manager = mgr

    handle_debug_command("plugin-errors")
    captured = capsys.readouterr()
    # Rich routes via stdout by default in test capture
    combined = captured.out + captured.err
    assert "Plugin errors" in combined
    assert "subscriber" in combined
    assert "session.start" in combined


def test_debug_command_no_errors_message(capsys):
    """With no errors captured, `/debug plugin-errors` emits a friendly dim note."""
    from aru.commands import handle_debug_command
    from aru.runtime import get_ctx

    ctx = get_ctx()
    ctx.plugin_manager = PluginManager()
    handle_debug_command("plugin-errors")
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "No plugin errors" in combined


def test_debug_command_unknown_subcommand(capsys):
    from aru.commands import handle_debug_command
    handle_debug_command("bogus")
    out = capsys.readouterr()
    assert "Unknown /debug subcommand" in (out.out + out.err)


def test_debug_command_no_args_usage(capsys):
    from aru.commands import handle_debug_command
    handle_debug_command("")
    out = capsys.readouterr()
    assert "Usage" in (out.out + out.err)
