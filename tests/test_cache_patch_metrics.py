"""Regression tests for per-call metric normalization in cache_patch.

Anthropic and OpenAI-compatible providers report prompt token usage with
*different* semantics, and both flow through Agno into the same
`MessageMetrics.input_tokens` / `cache_read_tokens` fields. Without
normalization, Aru's compaction trigger and context-window display
double-count the cached portion for OpenAI-style providers (OpenAI, Qwen,
DeepSeek, Groq, ...), firing compaction on every warm-cache turn.

Bug report: a Qwen3.6-Plus session compacted, ran a trivial follow-up turn,
and compacted *again* on a ~54K-token prompt because the window was read
as ~108K. Cause: `prompt_tokens` (total) + `cached_tokens` (subset) got
summed as if they were non-overlapping.

These tests drive `_patched_accumulate` directly with fake usage objects
so we don't need a real network call.
"""
from __future__ import annotations

import pytest

from aru import cache_patch
from aru.cache_patch import get_last_call_metrics


class _FakeUsage:
    """Mimics Agno's MessageMetrics shape after adapter population."""

    def __init__(self, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_tokens = cache_read_tokens
        self.cache_write_tokens = cache_write_tokens


class _FakeResponse:
    def __init__(self, usage):
        self.response_usage = usage


class _FakeAnthropicModel:
    def get_provider(self):
        return "Anthropic"


class _FakeOpenAIModel:
    def get_provider(self):
        return "OpenAI Chat"


@pytest.fixture
def patched_accumulate(monkeypatch):
    """Reach into cache_patch's installed patch and return it for direct calls.

    Apply the patch once if it isn't already, then grab the wrapper Agno's
    metrics module now points at.
    """
    cache_patch.apply_cache_patch()
    import agno.metrics as _metrics_module
    return _metrics_module.accumulate_model_metrics


def _reset_globals():
    cache_patch._last_call_input_tokens = 0
    cache_patch._last_call_output_tokens = 0
    cache_patch._last_call_cache_read = 0
    cache_patch._last_call_cache_write = 0


class TestAnthropicMetricsPassthrough:
    """Anthropic fields are already non-overlapping — leave them alone."""

    def test_anthropic_metrics_are_not_modified(self, patched_accumulate):
        _reset_globals()
        # Anthropic: input_tokens is *non-cached*. Full window is the sum.
        usage = _FakeUsage(
            input_tokens=3_500,
            output_tokens=200,
            cache_read_tokens=50_000,
            cache_write_tokens=0,
        )
        response = _FakeResponse(usage)
        patched_accumulate(response, _FakeAnthropicModel(), "model", run_metrics=None)

        input_t, output_t, cache_r, cache_w = get_last_call_metrics()
        assert input_t == 3_500, "Anthropic input_tokens must not be decremented"
        assert cache_r == 50_000
        assert input_t + output_t + cache_r + cache_w == 53_700


class TestOpenAIMetricsNormalized:
    """OpenAI-style: `input_tokens` = total prompt (cached + non-cached).
    Normalize by subtracting cache_read so the window formula works."""

    def test_warm_cache_is_not_double_counted(self, patched_accumulate):
        """The exact shape from the bug report.

        Raw: input_tokens=53_937, cache_read=53_890. Those overlap — the
        actual prompt is 53_937 tokens total. After normalization, the
        Aru-visible fields must reflect `input + cache_read = 53_937`, not
        107_827.
        """
        _reset_globals()
        usage = _FakeUsage(
            input_tokens=53_937,   # total prompt (OpenAI semantics)
            output_tokens=164,
            cache_read_tokens=53_890,  # subset of the above
            cache_write_tokens=0,
        )
        response = _FakeResponse(usage)
        patched_accumulate(response, _FakeOpenAIModel(), "model", run_metrics=None)

        input_t, output_t, cache_r, cache_w = get_last_call_metrics()
        # Normalized: non-cached portion only
        assert input_t == 47
        assert cache_r == 53_890
        # Window formula (input + cache_read + cache_write) now == real prompt
        assert input_t + cache_r + cache_w == 53_937
        # Total window used by compaction trigger
        assert input_t + output_t + cache_r + cache_w == 54_101

    def test_cold_cache_unchanged(self, patched_accumulate):
        """With no cache hit, normalization is a no-op."""
        _reset_globals()
        usage = _FakeUsage(
            input_tokens=53_896,
            output_tokens=79,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        response = _FakeResponse(usage)
        patched_accumulate(response, _FakeOpenAIModel(), "model", run_metrics=None)

        input_t, _, cache_r, _ = get_last_call_metrics()
        assert input_t == 53_896
        assert cache_r == 0

    def test_partial_cache_hit(self, patched_accumulate):
        """Mixed case: some of the prompt cached, some fresh."""
        _reset_globals()
        usage = _FakeUsage(
            input_tokens=10_000,   # total prompt
            output_tokens=100,
            cache_read_tokens=6_000,   # subset
            cache_write_tokens=0,
        )
        response = _FakeResponse(usage)
        patched_accumulate(response, _FakeOpenAIModel(), "model", run_metrics=None)

        input_t, _, cache_r, _ = get_last_call_metrics()
        assert input_t == 4_000, "4K fresh + 6K cached = 10K total"
        assert cache_r == 6_000
        assert input_t + cache_r == 10_000


class TestCompactionTriggerRegression:
    """End-to-end: normalized metrics must not fire compaction on a
    post-compact warm-cache turn."""

    def test_qwen_warm_cache_does_not_trip_compaction(self, patched_accumulate):
        from aru.context import should_compact

        _reset_globals()
        usage = _FakeUsage(
            input_tokens=53_937,
            output_tokens=164,
            cache_read_tokens=53_890,
            cache_write_tokens=0,
        )
        patched_accumulate(_FakeResponse(usage), _FakeOpenAIModel(), "model", run_metrics=None)

        input_t, output_t, cache_r, cache_w = get_last_call_metrics()
        last_call_window = input_t + output_t + cache_r + cache_w

        # ~54K on a 128K-context model (usable ~98K) — must NOT compact.
        assert last_call_window < 98_000, (
            f"Window was {last_call_window:,}; normalization failed — "
            f"prompt_tokens and cached_tokens are being double-counted."
        )
        assert should_compact(last_call_window, model_id="qwen3.6-plus") is False
