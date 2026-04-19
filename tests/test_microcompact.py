"""Tests for mid-turn micro-compaction in cache_patch.

Covers three layers added by the microcompact plan:

1. Allowlist (`COMPACTABLE_TOOLS` from context.py): only specific tool outputs
   are eligible for clearing. `delegate_task` and `invoke_skill` results MUST
   survive even when their content is past the 160K protect window.

2. Metrics (`get_microcompact_stats`): every prune invocation, every actual
   clear pass, and every cleared result is counted. Tests assert the counters
   move in lockstep with prune behavior.

3. Reactive overflow recovery (`_aggressive_prune` + `_is_context_overflow_error`):
   when a provider raises a context-too-long error, an aggressive prune fires
   that keeps only the last N compactable results — independent of the budget
   walk that the regular prune uses.

Tests construct minimal Agno-shaped Message objects directly (role + content +
tool_call_id) instead of going through full agent runs.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from aru import cache_patch
from aru.cache_patch import (
    _PRUNED_PLACEHOLDER,
    _aggressive_prune,
    _build_tool_id_to_name_map,
    _is_context_overflow_error,
    _prune_tool_messages,
    get_microcompact_stats,
    reset_microcompact_stats,
)
from aru.context import COMPACTABLE_TOOLS, PRUNE_PROTECTED_TOOLS


# ── Helpers ─────────────────────────────────────────────────────────

def _make_message(role: str, content=None, tool_call_id=None, tool_calls=None):
    """Build a SimpleNamespace shaped like Agno's Message for the parts the
    prune logic touches. Avoids importing the real Message class so tests
    stay decoupled from Agno's pydantic schema.
    """
    return SimpleNamespace(
        role=role,
        content=content,
        tool_call_id=tool_call_id,
        tool_calls=tool_calls,
        compressed_content=None,
    )


def _assistant_with_call(call_id: str, tool_name: str):
    """Assistant message announcing one tool call — needed so the id→name
    map can resolve `tool_call_id` back to a tool name.
    """
    return _make_message(
        role="assistant",
        tool_calls=[{
            "id": call_id,
            "type": "function",
            "function": {"name": tool_name, "arguments": "{}"},
        }],
    )


def _tool_result(call_id: str, content: str):
    return _make_message(role="tool", content=content, tool_call_id=call_id)


def _build_call_pair(call_id: str, tool_name: str, payload_chars: int):
    """Convenience: assistant call + matching tool_result with N chars payload."""
    return [
        _assistant_with_call(call_id, tool_name),
        _tool_result(call_id, "x" * payload_chars),
    ]


@pytest.fixture(autouse=True)
def _reset_stats():
    reset_microcompact_stats()
    yield
    reset_microcompact_stats()


# ── Allowlist ───────────────────────────────────────────────────────

class TestAllowlist:
    def test_compactable_tools_are_canonical_set(self):
        """Sanity: the allowlist contains the tools we expect to clear, and
        none of the protected ones. If this drifts, both the prune logic and
        documentation need a parallel update.
        """
        assert "read_file" in COMPACTABLE_TOOLS
        assert "bash" in COMPACTABLE_TOOLS
        assert "grep_search" in COMPACTABLE_TOOLS
        assert "delegate_task" not in COMPACTABLE_TOOLS
        assert "invoke_skill" not in COMPACTABLE_TOOLS
        # Protected list is the strict counterpart — it must NOT include
        # anything that's also compactable, or the prune would try to clear
        # what context.py says is sacred.
        assert COMPACTABLE_TOOLS.isdisjoint(PRUNE_PROTECTED_TOOLS)

    def test_id_to_name_map_resolves_function_call(self):
        msgs = [
            _assistant_with_call("call_1", "read_file"),
            _assistant_with_call("call_2", "delegate_task"),
        ]
        m = _build_tool_id_to_name_map(msgs)
        assert m == {"call_1": "read_file", "call_2": "delegate_task"}

    def test_delegate_task_never_pruned_even_past_budget(self):
        """Two tool results, both far past the 160K protect window. The
        compactable one must be cleared; the delegate_task one must survive
        — that's the entire reason for the allowlist.
        """
        # Overshoot the budget: 200K + 200K = 400K, well above 160K protect
        # AND above the 80K minimum-freeable threshold.
        msgs = []
        msgs += _build_call_pair("call_old", "delegate_task", 200_000)
        msgs += _build_call_pair("call_mid", "read_file", 200_000)
        # A recent compactable result that fits in the protect window.
        msgs += _build_call_pair("call_new", "read_file", 5_000)

        cleared = _prune_tool_messages(msgs)

        assert cleared == 1, "exactly one compactable result should be cleared"
        # delegate_task result still intact
        assert msgs[1].content == "x" * 200_000
        # mid read_file cleared
        assert msgs[3].content == _PRUNED_PLACEHOLDER
        # recent read_file untouched
        assert msgs[5].content == "x" * 5_000

    def test_unknown_tool_name_treated_as_non_compactable(self):
        """Defensive: if we can't resolve an id→name (e.g. malformed history),
        the result is treated as non-compactable. Better to leak budget than
        wipe something we can't classify.
        """
        # Tool result with no matching assistant call.
        msgs = [
            _tool_result("orphan_id", "x" * 300_000),
            *_build_call_pair("call_recent", "read_file", 5_000),
        ]
        cleared = _prune_tool_messages(msgs)
        assert cleared == 0
        assert msgs[0].content == "x" * 300_000

    def test_only_compactable_freeable_below_minimum_skips_prune(self):
        """When the freeable budget below the protect window is under
        PRUNE_MINIMUM_CHARS (80K), no prune fires — even if total is large
        because of non-compactable content above. This is the correctness
        side of the budget walk: don't churn the cache for tiny gains.
        """
        msgs = []
        # 200K of delegate_task (NOT freeable) + 30K of read_file (would be
        # freeable but below minimum) + 5K recent.
        msgs += _build_call_pair("c1", "delegate_task", 200_000)
        msgs += _build_call_pair("c2", "read_file", 30_000)
        msgs += _build_call_pair("c3", "read_file", 5_000)
        cleared = _prune_tool_messages(msgs)
        assert cleared == 0


# ── Metrics ─────────────────────────────────────────────────────────

class TestMetrics:
    def test_invocation_counter_increments_on_every_call(self):
        msgs = [_assistant_with_call("c1", "read_file"), _tool_result("c1", "small")]
        _prune_tool_messages(msgs)
        _prune_tool_messages(msgs)
        _prune_tool_messages(msgs)
        stats = get_microcompact_stats()
        assert stats["invocations"] == 3
        # No clear happened — content is too small.
        assert stats["clear_passes"] == 0
        assert stats["results_cleared"] == 0

    def test_clear_counters_track_actual_pruning(self):
        # Two old payloads of 200K each so both fall past the 160K protect
        # window AND together exceed the 80K freeable minimum. The 1K recent
        # pair stays in the window untouched.
        msgs = []
        msgs += _build_call_pair("old1", "read_file", 200_000)
        msgs += _build_call_pair("old2", "bash", 200_000)
        msgs += _build_call_pair("new", "read_file", 1_000)
        cleared = _prune_tool_messages(msgs)
        stats = get_microcompact_stats()
        assert cleared == 2
        assert stats["clear_passes"] == 1
        assert stats["results_cleared"] == 2

    def test_reset_zeroes_all_counters(self):
        # Prime some counts.
        msgs = []
        msgs += _build_call_pair("a", "read_file", 200_000)
        msgs += _build_call_pair("b", "read_file", 200_000)
        _prune_tool_messages(msgs)
        assert get_microcompact_stats()["results_cleared"] > 0
        reset_microcompact_stats()
        stats = get_microcompact_stats()
        assert stats == {
            "invocations": 0,
            "clear_passes": 0,
            "results_cleared": 0,
            "overflow_recoveries": 0,
        }


# ── Aggressive prune (overflow recovery body) ───────────────────────

class TestAggressivePrune:
    def test_keeps_only_last_n_compactable(self):
        msgs = []
        for i in range(10):
            msgs += _build_call_pair(f"c{i}", "read_file", 1_000)
        cleared = _aggressive_prune(msgs, keep_recent=3)
        # 10 results total → 7 cleared, 3 kept.
        assert cleared == 7
        cleared_contents = [
            m.content for m in msgs
            if m.role == "tool" and m.content == _PRUNED_PLACEHOLDER
        ]
        intact_contents = [
            m.content for m in msgs
            if m.role == "tool" and m.content != _PRUNED_PLACEHOLDER
        ]
        assert len(cleared_contents) == 7
        assert len(intact_contents) == 3

    def test_preserves_non_compactable_regardless_of_age(self):
        msgs = []
        msgs += _build_call_pair("delegate_old", "delegate_task", 1_000)
        for i in range(5):
            msgs += _build_call_pair(f"r{i}", "read_file", 1_000)
        _aggressive_prune(msgs, keep_recent=2)
        # delegate_task is at index 1 — must still hold its payload.
        assert msgs[1].content == "x" * 1_000

    def test_no_op_when_below_keep_threshold(self):
        msgs = []
        for i in range(2):
            msgs += _build_call_pair(f"c{i}", "read_file", 1_000)
        cleared = _aggressive_prune(msgs, keep_recent=3)
        assert cleared == 0


# ── Overflow detection ──────────────────────────────────────────────

class TestOverflowDetection:
    @pytest.mark.parametrize("phrase", [
        "prompt is too long: 250000 tokens",
        "Error: this model's maximum context length is 128000 tokens",
        "context_length_exceeded",
        "Request exceeds context window",
        "input is too long for this model",
        "request too large for tier",
    ])
    def test_known_signatures_match(self, phrase):
        exc = RuntimeError(phrase)
        assert _is_context_overflow_error(exc) is True

    def test_unrelated_error_does_not_match(self):
        assert _is_context_overflow_error(RuntimeError("rate limit exceeded")) is False
        assert _is_context_overflow_error(RuntimeError("invalid api key")) is False

    def test_walks_into_original_error(self):
        """Agno wraps provider errors — the trigger phrase often lives on the
        nested .original_error, not the outer ModelProviderError message.
        """
        inner = RuntimeError("prompt is too long")
        outer = RuntimeError("Model provider returned error")
        outer.original_error = inner
        assert _is_context_overflow_error(outer) is True
