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


class TestLiveMetricsAccumulation:
    """Regression: the status bar must climb during long implementation phases.

    ``_patched_accumulate`` now publishes ``metrics.updated`` and bumps
    ``session.total_*`` on every internal LLM call, so the TUI doesn't
    sit silent for minutes on tool-heavy turns. ``track_tokens`` at
    turn-end reconciles with Agno's cumulative metrics so nothing double-
    counts.
    """

    def _install_primary_ctx(self, monkeypatch):
        """Install a ctx with a real Session + stub plugin manager."""
        from aru.runtime import init_ctx
        from aru.session import Session

        ctx = init_ctx()
        ctx.session = Session()
        ctx.subagent_depth = 0
        published: list[tuple[str, dict]] = []

        class _StubMgr:
            loaded = True
            async def publish(self, event_type, data):
                published.append((event_type, data))

        ctx.plugin_manager = _StubMgr()
        return ctx, published

    @pytest.mark.asyncio
    async def test_session_totals_climb_per_call(self, patched_accumulate, monkeypatch):
        """Three internal calls land before the turn ends; totals track live."""
        ctx, published = self._install_primary_ctx(monkeypatch)
        _reset_globals()
        ctx.session.reset_live_token_counters()

        for call_in, call_out, cache_r in [(1_000, 50, 500), (800, 40, 200), (1_200, 60, 0)]:
            usage = _FakeUsage(
                input_tokens=call_in,
                output_tokens=call_out,
                cache_read_tokens=cache_r,
                cache_write_tokens=0,
            )
            patched_accumulate(
                _FakeResponse(usage), _FakeAnthropicModel(), "model", run_metrics=None
            )

        # Wait for the fire-and-forget publish tasks to drain.
        import asyncio
        for _ in range(10):
            await asyncio.sleep(0)

        s = ctx.session
        assert s.total_input_tokens == 3_000
        assert s.total_output_tokens == 150
        assert s.total_cache_read_tokens == 700
        # Live counters reflect the same deltas for turn-end reconciliation.
        assert s._live_input_added == 3_000
        assert s._live_output_added == 150
        # The event fired on each call, carrying the running totals.
        metrics_events = [d for name, d in published if name == "metrics.updated"]
        assert len(metrics_events) == 3
        assert metrics_events[-1]["total_input_tokens"] == 3_000
        assert metrics_events[-1]["estimated_cost"] >= 0.0

    @pytest.mark.asyncio
    async def test_track_tokens_does_not_double_count_after_live_updates(
        self, patched_accumulate, monkeypatch
    ):
        """Turn-end reconciliation: track_tokens adds only the unaccounted delta."""
        ctx, _ = self._install_primary_ctx(monkeypatch)
        _reset_globals()
        ctx.session.reset_live_token_counters()

        # Two live calls, then turn-end with Agno's cumulative matching exactly.
        for call_in, call_out in [(1_000, 50), (500, 25)]:
            usage = _FakeUsage(
                input_tokens=call_in,
                output_tokens=call_out,
                cache_read_tokens=0,
                cache_write_tokens=0,
            )
            patched_accumulate(
                _FakeResponse(usage), _FakeAnthropicModel(), "model", run_metrics=None
            )

        class _RunMetrics:
            input_tokens = 1_500
            output_tokens = 75
            cache_read_tokens = 0
            cache_write_tokens = 0

        ctx.session.track_tokens(_RunMetrics())

        s = ctx.session
        assert s.total_input_tokens == 1_500, "track_tokens must not re-add"
        assert s.total_output_tokens == 75
        assert s.api_calls == 1
        # Live counters reset so the next turn starts clean.
        assert s._live_input_added == 0
        assert s._live_output_added == 0

    def test_subagent_scope_skips_live_accumulation(self, patched_accumulate, monkeypatch):
        """Subagent API calls don't double-count the primary session.

        delegate_task adds the sub-run's tokens in one shot at completion,
        so ``_patched_accumulate`` must skip live updates when
        ``subagent_depth > 0``.
        """
        ctx, _ = self._install_primary_ctx(monkeypatch)
        ctx.subagent_depth = 1
        _reset_globals()
        ctx.session.reset_live_token_counters()

        usage = _FakeUsage(
            input_tokens=2_000,
            output_tokens=100,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        patched_accumulate(
            _FakeResponse(usage), _FakeAnthropicModel(), "model", run_metrics=None
        )

        s = ctx.session
        assert s.total_input_tokens == 0
        assert s._live_input_added == 0
        # Globals still updated — cache_patch-consuming code (compaction
        # trigger) keeps the usual visibility regardless of scope.
        in_t, _, _, _ = get_last_call_metrics()
        assert in_t == 2_000
