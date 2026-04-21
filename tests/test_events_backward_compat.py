"""Tests for backward-compat of plugin_manager.publish with typed events (E1)."""

from __future__ import annotations

import asyncio

import pytest

from aru.events import ToolCalledEvent
from aru.plugins.manager import PluginManager


async def test_publish_accepts_pydantic_model_and_coerces_to_dict():
    """Pydantic BaseEvent passed to publish() must reach subscribers as dict."""
    mgr = PluginManager()
    received: list[dict] = []

    def sub(payload):
        received.append(payload)

    mgr.subscribe("tool.called", sub)
    evt = ToolCalledEvent(tool_id="t1", tool_name="read_file", args={"path": "x"})
    await mgr.publish("tool.called", evt)

    assert len(received) == 1
    p = received[0]
    # Subscriber sees a plain dict, not the pydantic model
    assert isinstance(p, dict)
    assert p["event_type"] == "tool.called"
    assert p["tool_id"] == "t1"
    assert p["tool_name"] == "read_file"
    assert p["args"] == {"path": "x"}


async def test_publish_accepts_plain_dict_unchanged():
    """Legacy callers using dict payloads keep working."""
    mgr = PluginManager()
    received: list[dict] = []

    def sub(payload):
        received.append(payload)

    mgr.subscribe("file.changed", sub)
    await mgr.publish("file.changed", {"path": "foo.py", "operation": "write"})

    assert len(received) == 1
    p = received[0]
    assert p["event_type"] == "file.changed"
    assert p["path"] == "foo.py"
    assert p["operation"] == "write"


async def test_publish_accepts_none_data():
    mgr = PluginManager()
    received: list[dict] = []

    def sub(payload):
        received.append(payload)

    mgr.subscribe("turn.start", sub)
    await mgr.publish("turn.start", None)
    assert len(received) == 1
    assert received[0] == {"event_type": "turn.start"}


async def test_wildcard_subscribers_also_receive_dict():
    mgr = PluginManager()
    received: list[dict] = []

    def sub_all(payload):
        received.append(payload)

    mgr.subscribe_all(sub_all)
    await mgr.publish("tool.completed", ToolCalledEvent(tool_id="a", tool_name="bash"))
    # Even though we passed a model, wildcard sees the coerced dict
    assert len(received) == 1
    assert isinstance(received[0], dict)


async def test_async_subscriber_receives_dict():
    mgr = PluginManager()
    received: list[dict] = []

    async def sub(payload):
        received.append(payload)

    mgr.subscribe("tool.called", sub)
    await mgr.publish("tool.called", ToolCalledEvent(tool_id="z", tool_name="grep"))
    assert len(received) == 1
    assert received[0]["tool_id"] == "z"
