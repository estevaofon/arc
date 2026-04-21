"""Typed event schemas for the Aru plugin bus.

Pydantic models for each `event_type` in `plugins/hooks.py:VALID_HOOKS`.
Publishing code can emit either a plain dict (legacy) or one of these
models; `PluginManager.publish` coerces BaseModel -> dict via
`model_dump()` before fanning out so plugins that consume dicts keep
working unchanged.

Subscribers that want typed payloads can import the model and validate
themselves: `ToolCalledEvent.model_validate(payload)`.

The `event_type` field is a `Literal[...]` so static tools can discriminate
and the model's own field enforces the correct tag.
"""

from __future__ import annotations

import time
from typing import Any, Literal, Union

from pydantic import BaseModel, Field


class BaseEvent(BaseModel):
    """Shared fields. Subclasses override `event_type` with a Literal."""

    event_type: str
    timestamp: float = Field(default_factory=time.time)


# ── Chat / turn lifecycle ─────────────────────────────────────────────


class MessageUserEvent(BaseEvent):
    event_type: Literal["message.user"] = "message.user"
    message: str = ""
    session_id: str | None = None


class MessageAssistantEvent(BaseEvent):
    event_type: Literal["message.assistant"] = "message.assistant"
    content: str = ""
    session_id: str | None = None


class TurnStartEvent(BaseEvent):
    event_type: Literal["turn.start"] = "turn.start"
    session_id: str | None = None
    turn_index: int = 0
    user_message: str = ""


class TurnEndEvent(BaseEvent):
    event_type: Literal["turn.end"] = "turn.end"
    session_id: str | None = None
    turn_index: int = 0
    assistant_reply: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_ms: float = 0.0


# ── Tool lifecycle ────────────────────────────────────────────────────


class ToolCalledEvent(BaseEvent):
    event_type: Literal["tool.called"] = "tool.called"
    tool_id: str = ""
    tool_name: str = ""
    args: dict[str, Any] = Field(default_factory=dict)


class ToolCompletedEvent(BaseEvent):
    event_type: Literal["tool.completed"] = "tool.completed"
    tool_id: str = ""
    tool_name: str = ""
    result: Any = None
    duration_ms: float = 0.0
    error: str | None = None


# ── Sub-agent lifecycle ───────────────────────────────────────────────


class SubagentStartEvent(BaseEvent):
    event_type: Literal["subagent.start"] = "subagent.start"
    task_id: str = ""
    agent_kind: str = ""
    parent_task_id: str | None = None
    prompt: str = ""


class SubagentCompleteEvent(BaseEvent):
    event_type: Literal["subagent.complete"] = "subagent.complete"
    task_id: str = ""
    agent_kind: str = ""
    status: str = "ok"  # ok | error | cancelled
    result: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: float = 0.0


# ── Workspace / cwd ───────────────────────────────────────────────────


class CwdChangedEvent(BaseEvent):
    event_type: Literal["cwd.changed"] = "cwd.changed"
    old_cwd: str | None = None
    new_cwd: str = ""
    reason: str = ""
    branch: str | None = None


class FileChangedEvent(BaseEvent):
    event_type: Literal["file.changed"] = "file.changed"
    path: str = ""
    operation: str = ""  # write | edit | delete | patch


# ── Permission ────────────────────────────────────────────────────────


class PermissionDeniedEvent(BaseEvent):
    event_type: Literal["permission.denied"] = "permission.denied"
    category: str = ""
    subject: str = ""
    reason: str = ""


class PermissionModeChangedEvent(BaseEvent):
    """Published when ctx.permission_mode flips (default/acceptEdits/yolo)."""

    event_type: Literal["permission.mode.changed"] = "permission.mode.changed"
    old_mode: str = ""
    new_mode: str = ""


# ── Intra-turn metrics ────────────────────────────────────────────────


class MetricsUpdatedEvent(BaseEvent):
    """Published after each internal LLM API call (from ``cache_patch``).

    Lets the TUI refresh tokens/cost mid-turn so long implementation runs
    (many tool calls, many internal API calls, minutes between user
    prompts) don't sit silent on the status bar.
    """

    event_type: Literal["metrics.updated"] = "metrics.updated"
    session_id: str | None = None
    # Per-call figures (the call that just landed).
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    # Session cumulatives after this call has been added.
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    estimated_cost: float = 0.0


# ── Aggregate union (for subscribers that want exhaustive matching) ──


AruEvent = Union[
    MessageUserEvent,
    MessageAssistantEvent,
    TurnStartEvent,
    TurnEndEvent,
    ToolCalledEvent,
    ToolCompletedEvent,
    SubagentStartEvent,
    SubagentCompleteEvent,
    CwdChangedEvent,
    FileChangedEvent,
    PermissionDeniedEvent,
    PermissionModeChangedEvent,
    MetricsUpdatedEvent,
]


# Registry: event_type string -> model class. Used by PluginManager.publish
# to coerce untyped dicts to models (best-effort) so legacy publishers that
# still pass dicts get the validation for free when a model exists.
EVENT_MODELS: dict[str, type[BaseEvent]] = {
    "message.user": MessageUserEvent,
    "message.assistant": MessageAssistantEvent,
    "turn.start": TurnStartEvent,
    "turn.end": TurnEndEvent,
    "tool.called": ToolCalledEvent,
    "tool.completed": ToolCompletedEvent,
    "subagent.start": SubagentStartEvent,
    "subagent.complete": SubagentCompleteEvent,
    "cwd.changed": CwdChangedEvent,
    "file.changed": FileChangedEvent,
    "permission.denied": PermissionDeniedEvent,
    "permission.mode.changed": PermissionModeChangedEvent,
    "metrics.updated": MetricsUpdatedEvent,
}


def coerce_to_dict(data: BaseEvent | dict[str, Any] | None) -> dict[str, Any]:
    """Normalise event payload to a plain dict for the bus fan-out.

    BaseModel -> model_dump() (mode='python' keeps datetimes/etc native).
    dict -> returned unchanged.
    None -> empty dict.
    """
    if data is None:
        return {}
    if isinstance(data, BaseModel):
        return data.model_dump(mode="python")
    return data
