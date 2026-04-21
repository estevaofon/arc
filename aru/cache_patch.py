"""Monkey-patch Agno's model layer to reduce token consumption.

Four optimizations:

1. **Tool result pruning** (ALL providers): After each tool execution, old tool
   results in the message list are truncated to a short summary. This prevents
   O(n²) token growth where each API call re-sends all previous tool results.

2. **Cache breakpoints** (Anthropic only): Marks the last 2 messages with
   cache_control for Anthropic's prompt caching.

3. **Per-call metrics** (ALL providers): Captures input/output tokens of the
   last API call (context window size), exposed via get_last_call_metrics().

4. **Stop-reason capture** (Anthropic + OpenAI-compatible): Captures the
   `stop_reason` / `finish_reason` from the final message of the last API call,
   exposed via get_last_stop_reason(). Lets the runner detect `max_tokens`
   truncation and trigger the recovery loop.

These patches intercept Agno's internal loop so they work transparently
regardless of which provider is used.
"""

from __future__ import annotations

# Token-budget pruning (aligned with OpenCode's strategy):
# - Protect recent tool results within a token budget
# - Only prune if there's enough to free (avoid churn)
# - Walk backwards, protecting recent content first
# OpenCode uses 40K protect / 20K minimum; we use chars (~4 chars/token)
_PRUNE_PROTECT_CHARS = 160_000   # ~40K tokens — recent content always kept
_PRUNE_MINIMUM_CHARS = 80_000    # ~20K tokens — only prune if this much is freeable
_PRUNED_PLACEHOLDER = "[Old tool result cleared]"

# Last API call metrics (updated on every internal API call)
_last_call_input_tokens: int = 0
_last_call_output_tokens: int = 0
_last_call_cache_read: int = 0
_last_call_cache_write: int = 0

# Last API call stop reason (Anthropic uses "end_turn"/"tool_use"/"max_tokens"/
# "stop_sequence"/"pause_turn"; OpenAI uses "stop"/"length"/"tool_calls").
# We normalize "length" → "max_tokens" so callers can check a single value.
_last_call_stop_reason: str | None = None

# Micro-compaction metrics (process-wide, reset by tests via
# reset_microcompact_stats()). Recorded by _prune_tool_messages every time it
# fires from the format_function_call_results patch. Surfaced in /cost so
# users can see what the pre-API-call prune is actually doing — the basis
# for any future calibration of count/time-based triggers (Passos 3/4 of the
# plan, deferred until we have data here to justify them).
_microcompact_invocations: int = 0   # times _prune_tool_messages was called
_microcompact_clear_passes: int = 0  # times the prune actually cleared anything
_microcompact_results_cleared: int = 0  # cumulative tool_result blocks cleared

# Reactive overflow recovery: counts API calls where the provider rejected the
# request as too long and we wiped older tool_results then retried. Surfaced
# in /cost so users can tell when the recovery path is masking a chronically
# oversized context (suggests prune thresholds or model choice need attention).
_microcompact_overflow_recoveries: int = 0
# Aggressive prune keeps only the last N compactable tool_results, no matter
# the budget. Picked low because by definition we got here AFTER the regular
# prune (160K protect) failed to keep the context within model limits.
_OVERFLOW_RECOVERY_KEEP_RECENT = 3
# Substrings (case-insensitive) that mark a provider error as a context-too-long
# rejection. Anthropic / OpenAI / DashScope / DeepSeek / Groq all phrase it
# slightly differently; the union below covers the seen variants. Match is
# substring against str(exc) — wider than ideal, but the fallback path (no
# recovery) only kicks in when wrong, and a false positive at worst replays
# the same call after a no-op prune.
_OVERFLOW_ERROR_SIGNATURES = (
    "prompt is too long",
    "context length",
    "context_length_exceeded",
    "maximum context",
    "exceeds the maximum",
    "exceeds context",
    "input is too long",
    "too many tokens",
    "request too large",
)


def get_last_call_metrics() -> tuple[int, int, int, int]:
    """Return (input, output, cache_read, cache_write) from the most recent API call."""
    return _last_call_input_tokens, _last_call_output_tokens, _last_call_cache_read, _last_call_cache_write


def get_last_stop_reason() -> str | None:
    """Return the stop reason from the most recent API call, normalized.

    Returns one of: `end_turn`, `tool_use`, `max_tokens`, `stop_sequence`,
    `pause_turn`, or None if no call has happened yet / the provider did not
    expose one. OpenAI's `length` is mapped to `max_tokens` and `stop` to
    `end_turn` so callers have a single vocabulary.
    """
    return _last_call_stop_reason


def reset_last_stop_reason() -> None:
    """Clear the cached stop reason — call before starting a new turn so a
    stale value from a prior turn never leaks into the next one.
    """
    global _last_call_stop_reason
    _last_call_stop_reason = None


def get_microcompact_stats() -> dict:
    """Return process-wide micro-compaction metrics.

    Keys:
      - invocations: total times _prune_tool_messages ran
      - clear_passes: subset that actually cleared something
      - results_cleared: cumulative tool_result blocks wiped

    Used by /cost and tests. The ratio results_cleared/invocations is the
    natural calibration signal for whether the budget-based trigger fires
    often enough — if it's near zero across long sessions, the threshold
    is too lax (or the protect window too generous).
    """
    return {
        "invocations": _microcompact_invocations,
        "clear_passes": _microcompact_clear_passes,
        "results_cleared": _microcompact_results_cleared,
        "overflow_recoveries": _microcompact_overflow_recoveries,
    }


def reset_microcompact_stats() -> None:
    """Zero the micro-compaction counters. Test-only helper."""
    global _microcompact_invocations, _microcompact_clear_passes, _microcompact_results_cleared
    global _microcompact_overflow_recoveries
    _microcompact_invocations = 0
    _microcompact_clear_passes = 0
    _microcompact_results_cleared = 0
    _microcompact_overflow_recoveries = 0


def _is_context_overflow_error(exc) -> bool:
    """Return True iff `exc` looks like a provider context-too-long rejection.

    Substring match (case-insensitive) against the str of the exception and any
    nested `original_error` attribute. Wider than ideal but cheap; the recovery
    path that consumes this is itself idempotent (re-running with no changes
    after a no-op prune just hits the same error again and propagates).
    """
    msgs: list[str] = []
    try:
        msgs.append(str(exc))
    except Exception:
        pass
    inner = getattr(exc, "original_error", None) or getattr(exc, "__cause__", None)
    if inner is not None:
        try:
            msgs.append(str(inner))
        except Exception:
            pass
    blob = " ".join(m.lower() for m in msgs if m)
    return any(sig in blob for sig in _OVERFLOW_ERROR_SIGNATURES)


def _aggressive_prune(messages, keep_recent: int = _OVERFLOW_RECOVERY_KEEP_RECENT) -> int:
    """Wipe content of all but the last `keep_recent` compactable tool_results.

    Used reactively after a provider rejects a request as too long. Ignores the
    budget walk entirely — by the time we get here, the budget-based prune
    already failed to keep us under the model's context limit, so its answer
    is wrong for this request.

    Non-compactable tool_results (delegate_task etc.) are still preserved.
    Returns the number of results actually cleared.
    """
    from aru.context import COMPACTABLE_TOOLS

    id_to_name = _build_tool_id_to_name_map(messages)

    # Collect compactable tool_result indices in encounter order.
    compactable_indices: list[int] = []
    for i, msg in enumerate(messages):
        if getattr(msg, "role", None) != "tool":
            continue
        tc_id = getattr(msg, "tool_call_id", None)
        tool_name = id_to_name.get(tc_id) if tc_id else None
        if tool_name in COMPACTABLE_TOOLS:
            compactable_indices.append(i)

    if len(compactable_indices) <= keep_recent:
        return 0

    to_clear = compactable_indices[:-keep_recent] if keep_recent > 0 else compactable_indices
    cleared = 0
    for idx in to_clear:
        msg = messages[idx]
        content = getattr(msg, "content", None)
        if content is None or str(content) == _PRUNED_PLACEHOLDER:
            continue
        try:
            msg.content = _PRUNED_PLACEHOLDER
            if hasattr(msg, "compressed_content"):
                msg.compressed_content = None
            cleared += 1
        except (AttributeError, TypeError):
            pass
    return cleared


def _build_tool_id_to_name_map(messages) -> dict:
    """Walk assistant messages forward, building tool_call_id → tool_name map.

    Required because Agno's `role="tool"` Message carries `tool_call_id` but
    not the originating tool name — the name lives on the matching
    `assistant.tool_calls[i].function.name` in a previous message.
    """
    id_to_name: dict = {}
    for msg in messages:
        if getattr(msg, "role", None) != "assistant":
            continue
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            tc_id = tc.get("id") if isinstance(tc, dict) else None
            if not tc_id:
                continue
            fn = tc.get("function") if isinstance(tc, dict) else None
            tc_name = fn.get("name") if isinstance(fn, dict) else None
            if tc_name:
                id_to_name[tc_id] = tc_name
    return id_to_name


def _prune_tool_messages(messages):
    """Clear old tool result content using a token-budget approach.

    Walks backwards through messages, protecting recent content up to
    PRUNE_PROTECT_CHARS. Older tool results beyond that budget are replaced
    with a short placeholder. Only prunes if total freeable chars exceed
    PRUNE_MINIMUM_CHARS (avoids unnecessary churn on small conversations).

    Aligned with OpenCode's strategy: budget-based, not fixed-N.

    **Tool allowlist**: only outputs of tools in `COMPACTABLE_TOOLS` are
    eligible for clearing. Non-compactable tools (delegate_task, invoke_skill,
    tasklist mutators) still consume the protection budget but are never
    pruned — their content is semantically load-bearing. The id→name map is
    built from prior assistant `tool_calls` since `role="tool"` Messages carry
    only the call id, not the tool name. Single source of truth lives in
    `aru.context.COMPACTABLE_TOOLS`.

    Returns the number of tool results actually cleared (0 if none) for
    metrics consumption by `_microcompact_stats`.
    """
    from aru.context import COMPACTABLE_TOOLS

    global _microcompact_invocations, _microcompact_clear_passes, _microcompact_results_cleared
    _microcompact_invocations += 1

    id_to_name = _build_tool_id_to_name_map(messages)

    # Collect tool message indices, their content sizes, and compactability.
    tool_entries = []  # (index, content_len, is_compactable)
    for i, msg in enumerate(messages):
        if getattr(msg, "role", None) != "tool":
            continue
        content = getattr(msg, "content", None)
        content_len = len(str(content)) if content is not None else 0
        tc_id = getattr(msg, "tool_call_id", None)
        tool_name = id_to_name.get(tc_id) if tc_id else None
        # Defensive: if we can't resolve the name, treat as non-compactable.
        # Better to leak budget than wipe a delegate_task result by mistake.
        is_compactable = tool_name in COMPACTABLE_TOOLS if tool_name else False
        tool_entries.append((i, content_len, is_compactable))

    if not tool_entries:
        return 0

    # Walk backwards. ALL tool content (compactable or not) consumes the
    # protection budget — the prompt carries it either way. Once the budget
    # is exhausted, older entries are prune candidates ONLY if compactable;
    # non-compactable old entries (delegate_task etc.) stay untouched.
    running_total = 0
    prune_candidates = []  # (index, content_len) of compactable messages outside protection

    for idx, content_len, is_compactable in reversed(tool_entries):
        in_recent_window = (running_total + content_len) <= _PRUNE_PROTECT_CHARS
        running_total += content_len
        if not in_recent_window and is_compactable:
            prune_candidates.append((idx, content_len))

    # Only prune if there's enough to free
    freeable = sum(cl for _, cl in prune_candidates)
    if freeable < _PRUNE_MINIMUM_CHARS:
        return 0

    cleared = 0
    for idx, _ in prune_candidates:
        msg = messages[idx]
        content = getattr(msg, "content", None)
        if content is None:
            continue
        if str(content) == _PRUNED_PLACEHOLDER:
            continue
        try:
            msg.content = _PRUNED_PLACEHOLDER
            if hasattr(msg, "compressed_content"):
                msg.compressed_content = None
            cleared += 1
        except (AttributeError, TypeError):
            pass

    if cleared:
        _microcompact_clear_passes += 1
        _microcompact_results_cleared += cleared
    return cleared


_PATCH_APPLIED = False


def apply_cache_patch():
    """Apply all patches to reduce Agno's token consumption.

    Idempotent: wrapping Agno's base Model methods is additive, so
    calling this repeatedly (e.g. across a test suite's fixtures) would
    nest the wrappers and multiply every side effect — including the
    new per-call session token accumulation, which caused totals to
    grow by the wrap-depth instead of by the real per-call delta.
    """
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    _patch_tool_result_pruning()
    _patch_claude_cache_breakpoints()
    _patch_per_call_metrics()
    _patch_stop_reason_capture()
    _patch_overflow_recovery()
    _PATCH_APPLIED = True


def _patch_overflow_recovery():
    """Wrap Agno's retry loops to handle context-overflow rejections.

    When the provider rejects a request as too long (after the regular pre-call
    prune was insufficient), wipe content of all but the last
    `_OVERFLOW_RECOVERY_KEEP_RECENT` compactable tool_results in the message
    list and re-raise. Agno's existing retry loop in `_a*invoke_with_retry`
    will retry once with the now-shorter messages.

    Patches both `_ainvoke_with_retry` (non-stream) and
    `_ainvoke_stream_with_retry` (stream — what Aru's runner uses). Each is
    wrapped to call `_aggressive_prune` once per turn before the underlying
    retry fires; subsequent overflow errors propagate normally so we never
    loop forever wiping the same messages.

    A turn-scoped flag (`_overflow_recovery_done` set on the Model instance)
    ensures we only attempt recovery once per call site — if even the
    aggressive prune doesn't shrink the prompt enough, the error propagates
    and the user sees it instead of a silent retry storm.
    """
    from agno.models.base import Model
    from agno.exceptions import ModelProviderError

    _orig_ainvoke = Model._ainvoke_with_retry
    _orig_ainvoke_stream = Model._ainvoke_stream_with_retry

    async def _patched_ainvoke_with_retry(self, **kwargs):
        global _microcompact_overflow_recoveries
        try:
            return await _orig_ainvoke(self, **kwargs)
        except ModelProviderError as e:
            if not _is_context_overflow_error(e):
                raise
            messages = kwargs.get("messages")
            if messages is None:
                raise
            cleared = _aggressive_prune(messages)
            if cleared == 0:
                raise
            _microcompact_overflow_recoveries += 1
            return await _orig_ainvoke(self, **kwargs)

    async def _patched_ainvoke_stream_with_retry(self, **kwargs):
        global _microcompact_overflow_recoveries
        try:
            async for response in _orig_ainvoke_stream(self, **kwargs):
                yield response
            return
        except ModelProviderError as e:
            if not _is_context_overflow_error(e):
                raise
            messages = kwargs.get("messages")
            if messages is None:
                raise
            cleared = _aggressive_prune(messages)
            if cleared == 0:
                raise
            _microcompact_overflow_recoveries += 1
        # Retry once with the now-pruned messages. A second overflow propagates.
        async for response in _orig_ainvoke_stream(self, **kwargs):
            yield response

    Model._ainvoke_with_retry = _patched_ainvoke_with_retry
    Model._ainvoke_stream_with_retry = _patched_ainvoke_stream_with_retry


def _patch_tool_result_pruning():
    """Patch format_function_call_results to prune old tool results.

    This is called after each tool execution, right before the next API call.
    Works for ALL providers (Claude, OpenAI, Qwen, etc.) since it patches
    the base Model class.
    """
    from agno.models.base import Model

    _original_format_results = Model.format_function_call_results

    def _patched_format_results(self, messages, function_call_results, **kwargs):
        # First: prune old tool results already in messages
        _prune_tool_messages(messages)
        # Then: add new results normally
        return _original_format_results(self, messages, function_call_results, **kwargs)

    Model.format_function_call_results = _patched_format_results


def _patch_claude_cache_breakpoints():
    """Patch Claude's format_messages to add cache breakpoints.

    Marks the last 2 messages with cache_control for Anthropic's prompt
    caching. Non-Anthropic providers ignore these fields.
    """
    try:
        import agno.utils.models.claude as claude_utils
    except ImportError:
        return

    _original_format = claude_utils.format_messages

    def _patched_format_messages(messages, compress_tool_results=False):
        chat_messages, system_message = _original_format(
            messages, compress_tool_results=compress_tool_results
        )

        if not chat_messages:
            return chat_messages, system_message

        # Add cache_control to last 2 messages
        cache_marker = {"type": "ephemeral"}
        marked = 0
        for msg in reversed(chat_messages):
            if marked >= 2:
                break
            content = msg.get("content")
            if isinstance(content, list) and content:
                last_item = content[-1]
                if isinstance(last_item, dict):
                    last_item["cache_control"] = cache_marker
                    marked += 1
                elif hasattr(last_item, "type"):
                    try:
                        as_dict = last_item.model_dump() if hasattr(last_item, "model_dump") else dict(last_item)
                        as_dict["cache_control"] = cache_marker
                        content[-1] = as_dict
                        marked += 1
                    except Exception:
                        pass
            elif isinstance(content, str):
                msg["content"] = [{"type": "text", "text": content, "cache_control": cache_marker}]
                marked += 1

        return chat_messages, system_message

    claude_utils.format_messages = _patched_format_messages


def _publish_live_metrics(
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_write: int,
) -> None:
    """Apply this call's tokens to the primary session and publish ``metrics.updated``.

    Fires from inside ``_patched_accumulate`` after every internal LLM
    API call. Scoped to ``subagent_depth == 0`` so subagent calls are
    ignored here — their tokens are added in one shot by ``delegate_task``
    when the sub-run completes (doing both would double-count).

    On the primary session:
      * bumps ``total_*`` counters so ``estimated_cost`` climbs live;
      * updates ``last_*`` so the Last-context-window breakdown refreshes;
      * records the added delta in ``_live_*_added`` so ``track_tokens``
        at turn-end reconciles and never double-counts.

    The publish falls back silently when no plugin manager / no session
    is installed (tests, raw SDK use).
    """
    try:
        from aru.runtime import get_ctx, _schedule_publish
    except Exception:
        return
    try:
        ctx = get_ctx()
    except LookupError:
        return
    # Only the primary scope accumulates live — subagent tokens are
    # added wholesale by delegate_task at sub-run completion.
    if getattr(ctx, "subagent_depth", 0) != 0:
        return
    session = getattr(ctx, "session", None)
    if session is None:
        return
    try:
        session.total_input_tokens += input_tokens
        session.total_output_tokens += output_tokens
        session.total_cache_read_tokens += cache_read
        session.total_cache_write_tokens += cache_write
        session._live_input_added = (
            getattr(session, "_live_input_added", 0) + input_tokens
        )
        session._live_output_added = (
            getattr(session, "_live_output_added", 0) + output_tokens
        )
        session._live_cache_read_added = (
            getattr(session, "_live_cache_read_added", 0) + cache_read
        )
        session._live_cache_write_added = (
            getattr(session, "_live_cache_write_added", 0) + cache_write
        )
        session.last_input_tokens = input_tokens
        session.last_output_tokens = output_tokens
        session.last_cache_read = cache_read
        session.last_cache_write = cache_write
    except Exception:
        return
    try:
        cost = float(session.estimated_cost)
    except Exception:
        cost = 0.0
    _schedule_publish("metrics.updated", {
        "session_id": getattr(session, "session_id", None)
            or getattr(session, "id", None),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "total_input_tokens": session.total_input_tokens,
        "total_output_tokens": session.total_output_tokens,
        "total_cache_read_tokens": session.total_cache_read_tokens,
        "total_cache_write_tokens": session.total_cache_write_tokens,
        "estimated_cost": cost,
    })


def _patch_per_call_metrics():
    """Patch accumulate_model_metrics to capture per-API-call token counts.

    After each internal API call, Agno calls this function to sum tokens
    into RunMetrics. We intercept it to snapshot the last call's tokens,
    giving us the actual context window size (comparable to OpenCode/Claude Code).

    Provider semantics differ and must be normalized:

    - **Anthropic** reports `input_tokens` as *non-cached* only, with
      `cache_read_input_tokens` and `cache_creation_input_tokens` as
      separate, non-overlapping buckets. Total prompt =
      ``input + cache_read + cache_write``.
    - **OpenAI-compatible** (OpenAI, Qwen/Alibaba, DeepSeek, Groq, etc.)
      report `prompt_tokens` as the *total* prompt, with
      `prompt_tokens_details.cached_tokens` being a *subset* of that total.
      Total prompt = ``input`` alone; ``cache_read`` is already inside it.

    Agno's adapters populate `metrics.input_tokens` from each provider's
    native field without normalizing, so the same name means different
    things. That would double-count cached tokens for OpenAI-style providers
    in any formula that does ``input + cache_read``. To keep the rest of
    Aru provider-agnostic, normalize here: subtract `cache_read` from
    `input_tokens` whenever the provider overlaps them, so downstream code
    can always treat `(input, cache_read, cache_write)` as non-overlapping
    and sum them safely.
    """
    from agno.metrics import accumulate_model_metrics as _original_accumulate

    import agno.metrics as _metrics_module

    def _patched_accumulate(model_response, model, model_type, run_metrics=None):
        global _last_call_input_tokens, _last_call_output_tokens
        global _last_call_cache_read, _last_call_cache_write
        usage = getattr(model_response, "response_usage", None)
        if usage is not None:
            input_tokens = getattr(usage, "input_tokens", 0) or 0
            output_tokens = getattr(usage, "output_tokens", 0) or 0
            cache_read = getattr(usage, "cache_read_tokens", 0) or 0
            cache_write = getattr(usage, "cache_write_tokens", 0) or 0

            # For non-Anthropic providers, `input_tokens` already includes
            # the cached portion, so subtract it to match Anthropic's
            # non-overlapping semantics. See docstring above.
            try:
                provider_name = model.get_provider() if hasattr(model, "get_provider") else ""
            except Exception:
                provider_name = ""
            is_anthropic = "anthropic" in (provider_name or "").lower()
            if not is_anthropic and cache_read and input_tokens >= cache_read:
                input_tokens -= cache_read

            _last_call_input_tokens = input_tokens
            _last_call_output_tokens = output_tokens
            _last_call_cache_read = cache_read
            _last_call_cache_write = cache_write

            # Intra-turn live session update + bus publish. Gated to the
            # primary agent (subagent_depth == 0) so subagent API calls
            # don't double-count — delegate_task adds subagent totals in
            # one shot when the sub-run completes.
            _publish_live_metrics(
                input_tokens, output_tokens, cache_read, cache_write
            )
        return _original_accumulate(model_response, model, model_type, run_metrics)

    _metrics_module.accumulate_model_metrics = _patched_accumulate

    # Also patch the reference in base.py since it may have imported directly
    try:
        import agno.models.base as _base_module
        _base_module.accumulate_model_metrics = _patched_accumulate
    except (ImportError, AttributeError):
        pass


# OpenAI "length" and Anthropic "max_tokens" mean the same thing; normalize so
# runner logic can check a single value.
_STOP_REASON_NORMALIZE = {
    "length": "max_tokens",        # OpenAI
    "stop": "end_turn",            # OpenAI
    "tool_calls": "tool_use",      # OpenAI
    "function_call": "tool_use",   # legacy OpenAI
    "MAX_TOKENS": "max_tokens",    # Gemini (all-caps)
}


def _record_stop_reason(raw: str | None) -> None:
    """Normalize and cache the provider's stop reason."""
    global _last_call_stop_reason
    if raw is None or raw == "":
        return
    _last_call_stop_reason = _STOP_REASON_NORMALIZE.get(raw, raw)


def _patch_stop_reason_capture():
    """Forward `stop_reason` from Agno's provider parsers into a module-level
    slot readable via `get_last_stop_reason()`.

    Agno's Anthropic adapter sees `response.stop_reason` (non-streaming) and
    `response.message.stop_reason` (streaming MessageStopEvent), but discards
    both before anything downstream can observe them. We wrap the two parsers
    and record the value as a side effect. The OpenAI-compatible adapter
    already exposes `response.choices[0].finish_reason`, so we hook that too
    for completeness (Qwen, DeepSeek, Groq, OpenRouter).
    """
    # Anthropic (native + streaming)
    try:
        from agno.models.anthropic import claude as _claude_mod

        _original_parse = _claude_mod.Claude._parse_provider_response
        _original_parse_delta = _claude_mod.Claude._parse_provider_response_delta

        def _patched_parse(self, response, *args, **kwargs):
            result = _original_parse(self, response, *args, **kwargs)
            _record_stop_reason(getattr(response, "stop_reason", None))
            return result

        def _patched_parse_delta(self, response, *args, **kwargs):
            result = _original_parse_delta(self, response, *args, **kwargs)
            # MessageStopEvent / ParsedBetaMessageStopEvent carry the final
            # stop_reason on their nested `message` object.
            msg = getattr(response, "message", None)
            if msg is not None:
                _record_stop_reason(getattr(msg, "stop_reason", None))
            return result

        _claude_mod.Claude._parse_provider_response = _patched_parse
        _claude_mod.Claude._parse_provider_response_delta = _patched_parse_delta
    except (ImportError, AttributeError):
        pass

    # OpenAI-compatible (OpenAI, Qwen/DashScope, DeepSeek, Groq, OpenRouter)
    try:
        from agno.models.openai import chat as _openai_chat

        _original_openai_parse = _openai_chat.OpenAIChat._parse_provider_response

        def _patched_openai_parse(self, response, *args, **kwargs):
            result = _original_openai_parse(self, response, *args, **kwargs)
            try:
                choice = response.choices[0]
                _record_stop_reason(getattr(choice, "finish_reason", None))
            except (AttributeError, IndexError, TypeError):
                pass
            return result

        _openai_chat.OpenAIChat._parse_provider_response = _patched_openai_parse

        if hasattr(_openai_chat.OpenAIChat, "_parse_provider_response_delta"):
            _original_openai_delta = _openai_chat.OpenAIChat._parse_provider_response_delta

            def _patched_openai_delta(self, response, *args, **kwargs):
                result = _original_openai_delta(self, response, *args, **kwargs)
                try:
                    choice = response.choices[0]
                    # Only the final chunk sets finish_reason.
                    _record_stop_reason(getattr(choice, "finish_reason", None))
                except (AttributeError, IndexError, TypeError):
                    pass
                return result

            _openai_chat.OpenAIChat._parse_provider_response_delta = _patched_openai_delta
    except (ImportError, AttributeError):
        pass
