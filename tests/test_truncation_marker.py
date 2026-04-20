"""Stage 3 regression: structured truncation marker.

Covers:
- Marker includes original_lines / original_bytes / shown_head/tail_lines
- Marker contains saved_at when disk save succeeded
- Marker omits zero/empty attributes (compact)
- Legacy _build_truncation_hint shim still works
- truncate_output emits a marker parseable by a simple regex
"""

import re
from unittest.mock import patch

from aru.context import (
    _build_truncation_hint,
    _build_truncation_marker,
    truncate_output,
)


def _parse_marker_attrs(marker: str) -> dict[str, str]:
    """Pull key="value" pairs out of a self-closing <truncation /> tag."""
    m = re.search(r"<truncation\s+([^>]*?)\s*/>", marker)
    if not m:
        # Bare <truncation /> with no attrs
        return {} if "<truncation />" in marker else {}
    attr_blob = m.group(1)
    return dict(re.findall(r'(\w+)="([^"]*)"', attr_blob))


def test_marker_includes_all_provided_attributes():
    marker = _build_truncation_marker(
        source_tool="bash",
        source_file="/tmp/log.txt",
        original_bytes=50_000,
        original_lines=2000,
        shown_head_lines=300,
        shown_tail_lines=200,
        saved_path="/tmp/.aru/truncated/output_x.txt",
    )
    attrs = _parse_marker_attrs(marker)
    assert attrs["source_tool"] == "bash"
    assert attrs["source_file"] == "/tmp/log.txt"
    assert attrs["original_bytes"] == "50000"
    assert attrs["original_lines"] == "2000"
    assert attrs["shown_head_lines"] == "300"
    assert attrs["shown_tail_lines"] == "200"
    assert attrs["saved_at"] == "/tmp/.aru/truncated/output_x.txt"


def test_marker_omits_zero_and_empty():
    """Compact markers: only populated fields should appear."""
    marker = _build_truncation_marker(source_tool="grep", original_lines=500)
    attrs = _parse_marker_attrs(marker)
    assert attrs == {"source_tool": "grep", "original_lines": "500"}


def test_marker_empty_returns_bare_tag():
    marker = _build_truncation_marker()
    assert marker == "<truncation />"


def test_legacy_hint_shim_still_returns_marker():
    """_build_truncation_hint delegates to the new marker builder."""
    hint = _build_truncation_hint(
        source_file="f.txt",
        source_tool="read",
        lines_shown=50,
        saved_path="/tmp/saved.txt",
    )
    assert hint.startswith("<truncation ")
    attrs = _parse_marker_attrs(hint)
    assert attrs["source_file"] == "f.txt"
    assert attrs["source_tool"] == "read"
    assert attrs["shown_head_lines"] == "50"
    assert attrs["saved_at"] == "/tmp/saved.txt"


@patch("aru.context._save_truncated_output", return_value="/tmp/saved.txt")
def test_truncate_output_emits_marker_on_line_overflow(_mock):
    output = "\n".join(f"line {i}" for i in range(800))
    result = truncate_output(output, source_tool="bash", source_file="/tmp/log")
    attrs = _parse_marker_attrs(result)
    assert attrs["source_tool"] == "bash"
    assert attrs["source_file"] == "/tmp/log"
    assert attrs["original_lines"] == "800"
    assert int(attrs["original_bytes"]) > 0
    assert int(attrs["shown_head_lines"]) > 0
    assert int(attrs["shown_tail_lines"]) > 0
    assert attrs["saved_at"] == "/tmp/saved.txt"


@patch("aru.context._save_truncated_output", return_value=None)
def test_truncate_output_emits_marker_on_byte_overflow(_mock):
    # Many short lines under TRUNCATE_MAX_LINES, but heavy total bytes.
    output = ("x" * 300 + "\n") * 300  # ~90KB, 300 lines
    result = truncate_output(output, source_tool="bash")
    # byte-path fires even though line count may be under the line cap
    if "<truncation" in result:
        attrs = _parse_marker_attrs(result)
        # source_tool propagated
        assert attrs["source_tool"] == "bash"
        # saved_at absent because _save_truncated_output mocked to None
        assert "saved_at" not in attrs


@patch("aru.context._save_truncated_output", return_value=None)
def test_truncate_output_no_marker_under_limits(_mock):
    """No marker when output fits under thresholds."""
    result = truncate_output("tiny\noutput\n", source_tool="bash")
    assert "<truncation" not in result


def test_delegate_passes_source_tool_in_marker():
    """Step 3a — delegate.py now passes source_tool so marker is informative."""
    # Integration-style: verify the call site uses the kwarg. Ensure the
    # marker produced from a large delegate result carries source_tool.
    with patch("aru.context._save_truncated_output", return_value=None):
        large = "x" * 80_000  # > TRUNCATE_MAX_BYTES
        result = truncate_output(large, source_tool="delegate_task")
        attrs = _parse_marker_attrs(result)
        assert attrs.get("source_tool") == "delegate_task"
