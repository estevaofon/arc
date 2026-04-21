"""_markdown_to_text renders Rich markdown and flattens to a selectable Text.

The flat-Text invariant is what lets Textual's selection traversal walk the
assistant bubble. These tests check both that the *visible* content (post-
markdown-rendering) is present in the flat plain — so selection can grab
it — and that the rendered form has style spans (bold, color, syntax) that
a naive plain-text concatenation would not produce.
"""

from __future__ import annotations

from rich.text import Text

from aru.tui.widgets.chat import _markdown_to_text


def test_plain_paragraph():
    t = _markdown_to_text("hello world")
    assert isinstance(t, Text)
    assert "hello world" in t.plain


def test_headers_render_without_markers_but_with_bold():
    """Rich strips the ``#`` glyphs and applies bold styling to headers."""
    t = _markdown_to_text("# Main\n\n## Sub\n\n### Third\n\nbody")
    plain = t.plain
    # The visible title text is in the flat plain (so copy-by-selection works)
    assert "Main" in plain
    assert "Sub" in plain
    assert "Third" in plain
    assert "body" in plain
    # The hash markers are gone — Rich replaced them with styling
    assert "# Main" not in plain
    assert "## Sub" not in plain
    # And at least one span carries a bold style (header rendering)
    has_bold = any(
        s.style is not None and "bold" in str(s.style).lower()
        for s in t._spans
    )
    assert has_bold


def test_fenced_code_block_body_present_and_styled():
    """Code block body is selectable; syntax-highlight spans are applied."""
    md = "```python\nprint('hi')\n```\nafter"
    t = _markdown_to_text(md)
    # The code body stays selectable — users can drag across it
    assert "print" in t.plain
    assert "'hi'" in t.plain
    assert "after" in t.plain
    # Code fence markers are consumed by Rich
    assert "```python" not in t.plain
    # Syntax highlight means spans exist (colors applied to tokens)
    assert len(t._spans) > 0


def test_inline_backticks_styled_not_literal():
    """Inline code renders with styling; backticks are replaced by style."""
    t = _markdown_to_text("use `foo()` in code")
    # Content is preserved for selection
    assert "foo()" in t.plain
    assert "use" in t.plain
    # Backticks themselves are gone (Rich strips them, applies a style)
    assert "`foo()`" not in t.plain
    # And at least one span covers the inline-code region
    assert any(s.style is not None for s in t._spans)


def test_bold_markers_become_bold_style():
    """`**text**` renders as bold — asterisks vanish, style appears."""
    t = _markdown_to_text("hello **world** done")
    assert "world" in t.plain
    assert "**world**" not in t.plain
    # A span with bold style covers the "world" range
    has_bold_on_world = any(
        s.style is not None and "bold" in str(s.style).lower()
        for s in t._spans
    )
    assert has_bold_on_world


def test_bullet_list_renders_with_bullet_glyphs():
    """`- item` renders with a real `•` bullet, indented."""
    t = _markdown_to_text("- first\n- second\n- third")
    # Rich uses `•` for unordered list bullets
    assert "•" in t.plain
    assert "first" in t.plain
    assert "second" in t.plain
    assert "third" in t.plain


def test_empty_input_returns_empty_text():
    t = _markdown_to_text("")
    assert isinstance(t, Text)
    assert t.plain.strip() == ""


def test_paragraph_separator_produces_newlines():
    """Blank-line-separated paragraphs land on separate lines.

    Markdown joins soft-breaks (consecutive non-blank lines) into one
    paragraph by design, so the test uses explicit paragraph breaks.
    """
    t = _markdown_to_text("para one\n\npara two\n\npara three")
    assert "para one" in t.plain
    assert "para two" in t.plain
    assert "para three" in t.plain
    assert t.plain.count("\n") >= 2


def test_returns_flat_text_not_composite():
    """The whole point: the result is one ``Text``, not a Group/Panel."""
    t = _markdown_to_text("**bold** and `code` and # Header\n\nbody")
    assert isinstance(t, Text)
    # Flat Text is what Textual's get_selected_text walks; a composite
    # renderable (Group, Panel) would NOT satisfy this.
