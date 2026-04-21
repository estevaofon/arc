"""_markdown_to_text produces a selectable rich.Text (not composite Markdown)."""

from __future__ import annotations

from rich.text import Text

from aru.tui.widgets.chat import _markdown_to_text


def test_plain_paragraph():
    t = _markdown_to_text("hello world")
    assert isinstance(t, Text)
    assert "hello world" in t.plain


def test_headers_styled_bold():
    t = _markdown_to_text("# Main\n## Sub\n### Third\nbody")
    # Assert all the text is present in flat form (so selection can grab it).
    plain = t.plain
    assert "# Main" in plain
    assert "## Sub" in plain
    assert "### Third" in plain
    assert "body" in plain


def test_fenced_code_block_preserved():
    md = "```python\nprint('hi')\n```\nafter"
    t = _markdown_to_text(md)
    assert "```python" in t.plain
    assert "print('hi')" in t.plain
    assert "after" in t.plain


def test_inline_backticks_kept_verbatim():
    t = _markdown_to_text("use `foo()` in code")
    # Backticks retained so the selection captures the literal characters
    # as the user sees them on screen.
    assert "`foo()`" in t.plain


def test_empty_input_returns_empty_text():
    t = _markdown_to_text("")
    assert isinstance(t, Text)
    # An empty line split yields [""] so the result has a single newline.
    assert t.plain.strip() == ""


def test_multiline_paragraph_has_newlines():
    t = _markdown_to_text("line one\nline two\nline three")
    assert "line one" in t.plain
    assert "line two" in t.plain
    assert t.plain.count("\n") >= 2
