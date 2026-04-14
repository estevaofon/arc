"""Agent creation: catalog-driven factory plus custom agent instantiation."""

from __future__ import annotations

import functools
import inspect
import logging

from agno.compression.manager import CompressionManager
from agno.utils.log import log_warning

from aru.agents.base import build_instructions as _build_instructions
from aru.agents.catalog import AGENTS, AgentSpec
from aru.config import AgentConfig, CustomAgent
from aru.providers import create_model
from aru.session import Session

logger = logging.getLogger("aru.agent_factory")

# Max chars for truncation fallback when compression fails
_TRUNCATE_FALLBACK = 3000


class _SafeCompressionManager(CompressionManager):
    """CompressionManager that truncates on failure instead of leaving messages uncompressed.

    Agno's default behavior: if compression returns None, the message stays with
    compressed_content=None → should_compress() fires again → infinite retry loop.
    This subclass marks failed messages with a truncated version so the loop moves on.
    """

    async def acompress(self, messages, run_metrics=None):
        before = {id(m) for m in messages if m.role == "tool" and m.compressed_content is None}
        await super().acompress(messages, run_metrics=run_metrics)
        for msg in messages:
            if id(msg) in before and msg.compressed_content is None:
                content_str = str(msg.content or "")
                msg.compressed_content = content_str[:_TRUNCATE_FALLBACK] + (
                    "... [truncated, compression failed]" if len(content_str) > _TRUNCATE_FALLBACK else ""
                )
                log_warning(f"Compression fallback (truncate) for {msg.tool_name}")


def _wrap_tools_with_hooks(tools: list) -> list:
    """Wrap tool functions to fire tool.execute.before/after plugin hooks.

    Before hook can mutate args; after hook can mutate the result.
    If a before hook raises, the tool is not executed and the error is returned.
    """
    from aru.runtime import get_ctx

    async def _fire(event_name: str, data: dict) -> dict:
        try:
            ctx = get_ctx()
            mgr = ctx.plugin_manager
            if mgr is not None and mgr.loaded:
                event = await mgr.fire(event_name, data)
                return event.data
        except (LookupError, AttributeError):
            pass
        return data

    async def _fire_tool_definition(tool_name: str, description: str, parameters: dict) -> dict:
        """Fire tool.definition hook — plugins can modify tool desc/params."""
        try:
            ctx = get_ctx()
            mgr = ctx.plugin_manager
            if mgr is not None and mgr.loaded:
                event = await mgr.fire("tool.definition", {
                    "tool_name": tool_name,
                    "description": description,
                    "parameters": parameters,
                })
                return event.data
        except (LookupError, AttributeError):
            pass
        return {"tool_name": tool_name, "description": description, "parameters": parameters}

    def _wrap_one(fn):
        if not callable(fn) or getattr(fn, "_hook_wrapped", False):
            return fn

        @functools.wraps(fn)
        async def wrapper(**kwargs):
            tool_name = fn.__name__
            # Before hook — plugins can mutate args or raise PermissionError to block
            try:
                before_data = await _fire("tool.execute.before", {
                    "tool_name": tool_name,
                    "args": kwargs,
                })
                kwargs = before_data.get("args", kwargs)
            except PermissionError as e:
                return f"BLOCKED by plugin: {e}. Do NOT retry this operation."

            # Execute the tool
            if inspect.iscoroutinefunction(fn):
                result = await fn(**kwargs)
            else:
                result = fn(**kwargs)

            # After hook — plugins can mutate the result
            after_data = await _fire("tool.execute.after", {
                "tool_name": tool_name,
                "args": kwargs,
                "result": result,
            })
            return after_data.get("result", result)

        wrapper._hook_wrapped = True
        return wrapper

    return [_wrap_one(t) for t in tools]


def _fire_sync_hook(event_name: str, data: dict) -> dict:
    """Fire a plugin hook synchronously (for agent creation context).

    Agent creation happens in sync code, so we need a sync path.
    """
    try:
        from aru.runtime import get_ctx
        ctx = get_ctx()
        mgr = ctx.plugin_manager
        if mgr is not None and mgr.loaded:
            import asyncio
            from aru.plugins.hooks import HookEvent
            event = HookEvent(hook=event_name, data=data or {})
            for hooks in mgr._hooks:
                for handler in hooks.get_handlers(event_name):
                    try:
                        if asyncio.iscoroutinefunction(handler):
                            # Best-effort: try to run async handler
                            try:
                                loop = asyncio.get_running_loop()
                            except RuntimeError:
                                loop = None
                            if loop and loop.is_running():
                                # Can't await in sync context with running loop — skip
                                continue
                            else:
                                asyncio.run(handler(event))
                        else:
                            handler(event)
                    except Exception as e:
                        logger.warning("Hook handler error (%s): %s", event_name, e)
            return event.data
    except (LookupError, AttributeError):
        pass
    return data


def _apply_chat_hooks(instructions: str, model_ref: str, agent_name: str,
                      max_tokens: int = 8192) -> tuple[str, str, int]:
    """Apply chat.system.transform and chat.params hooks to agent creation params.

    Returns (instructions, model_ref, max_tokens) — possibly modified by plugins.
    """
    # chat.system.transform — plugins can modify the system prompt
    data = _fire_sync_hook("chat.system.transform", {
        "system_prompt": instructions,
        "agent": agent_name,
    })
    instructions = data.get("system_prompt", instructions)

    # chat.params — plugins can modify LLM parameters
    data = _fire_sync_hook("chat.params", {
        "model": model_ref,
        "max_tokens": max_tokens,
        "temperature": None,  # let plugin set if desired
    })
    model_ref = data.get("model", model_ref)
    max_tokens = data.get("max_tokens", max_tokens)

    return instructions, model_ref, max_tokens


def _make_compression_manager() -> _SafeCompressionManager:
    """Construct the safe compression manager used for every native agent."""
    from aru.runtime import get_ctx
    return _SafeCompressionManager(
        model=create_model(get_ctx().small_model_ref, max_tokens=2048),
        compress_tool_results=True,
        compress_tool_results_limit=25,
    )


def create_agent_from_spec(
    spec: AgentSpec,
    session: Session | None = None,
    model_ref: str | None = None,
    extra_instructions: str = "",
):
    """Build an Agno Agent from a catalog spec.

    Single construction path for all native agents (build/plan/executor/explorer).
    Resolves model, wraps tools with plugin hooks, applies chat.system.transform
    and chat.params hooks, and attaches the safe compression manager.

    `session` may be None for subagent specs that always use the small model.
    """
    from agno.agent import Agent
    from aru.runtime import get_ctx

    if spec.small_model:
        resolved_model = model_ref or get_ctx().small_model_ref
    else:
        if session is None:
            raise ValueError(f"AgentSpec {spec.name!r} requires a session to resolve the model")
        resolved_model = model_ref or session.model_ref

    tools = _wrap_tools_with_hooks(spec.tools_factory())
    instructions = _build_instructions(spec.role, extra_instructions)

    instructions, resolved_model, max_tokens = _apply_chat_hooks(
        instructions, resolved_model, spec.name, max_tokens=spec.max_tokens,
    )

    return Agent(
        name=spec.name,
        model=create_model(resolved_model, max_tokens=max_tokens),
        tools=tools,
        instructions=instructions,
        markdown=True,
        compress_tool_results=True,
        compression_manager=_make_compression_manager(),
        tool_call_limit=None,
    )


def create_general_agent(
    session: Session,
    config: AgentConfig | None = None,
    model_override: str | None = None,
    env_context: str = "",
):
    """Create the general-purpose agent (thin wrapper around the catalog factory)."""
    extra = config.get_extra_instructions() if config else ""
    if env_context:
        extra = f"{extra}\n\n{env_context}" if extra else env_context
    return create_agent_from_spec(
        AGENTS["build"],
        session,
        model_ref=model_override or session.model_ref,
        extra_instructions=extra,
    )


def create_custom_agent_instance(agent_def: CustomAgent, session: Session,
                                  config: AgentConfig | None = None,
                                  env_context: str = ""):
    """Create an Agno Agent from a CustomAgent definition."""
    from agno.agent import Agent
    from aru.agents.base import BASE_INSTRUCTIONS
    from aru.tools.codebase import resolve_tools

    model_ref = agent_def.model or session.model_ref
    tools = _wrap_tools_with_hooks(resolve_tools(agent_def.tools))

    extra = config.get_extra_instructions() if config else ""
    if env_context:
        extra = f"{extra}\n\n{env_context}" if extra else env_context
    parts = [agent_def.system_prompt, BASE_INSTRUCTIONS]
    if extra:
        parts.append(extra)
    instructions = "\n\n".join(parts)

    # Apply chat hooks (system.transform + params)
    instructions, model_ref, max_tokens = _apply_chat_hooks(
        instructions, model_ref, agent_def.name, max_tokens=8192,
    )

    return Agent(
        name=agent_def.name,
        model=create_model(model_ref, max_tokens=max_tokens),
        tools=tools,
        instructions=instructions,
        markdown=True,
        tool_call_limit=agent_def.max_turns,
    )
