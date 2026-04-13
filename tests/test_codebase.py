import pytest
from pathlib import Path
from aru.tools.codebase import (
    write_file, glob_search, read_file, grep_search,
    edit_file, list_directory,
    get_project_tree, _is_long_running,
    _html_to_text, clear_read_cache,
    _format_unified_diff,
    resolve_tools, TOOL_REGISTRY, GENERAL_TOOLS,
    delegate_task, set_custom_agents,
)
from aru.permissions import (
    set_skip_permissions, get_skip_permissions, reset_session,
    _shell_split, resolve_permission,
)
from aru.runtime import get_ctx


def test_write_file_creates_file(tmp_path):
    '''Test that write_file creates a new file with correct content.'''
    target = tmp_path / "newfile.txt"

    set_skip_permissions(True)
    try:
        result = write_file(str(target), "hello world")
    finally:
        set_skip_permissions(False)

    assert target.exists()
    assert target.read_text() == "hello world"
    assert "successfully" in result.lower() or "wrote" in result.lower()


def test_glob_search(temp_dir):
    """Test glob_search returns correct matches for given patterns."""
    # Create files with known names/extensions
    (temp_dir / "main.py").write_text("# main")
    (temp_dir / "utils.py").write_text("# utils")
    (temp_dir / "README.md").write_text("# readme")
    sub = temp_dir / "src"
    sub.mkdir()
    (sub / "app.py").write_text("# app")
    (sub / "config.json").write_text("{}")

    # **/*.py should match all .py files recursively
    result = glob_search("**/*.py", directory=str(temp_dir))
    assert "main.py" in result or any("main.py" in r for r in result.splitlines())
    assert "utils.py" in result or any("utils.py" in r for r in result.splitlines())
    assert any("app.py" in r for r in result.splitlines())
    # .md files should not appear in **/*.py results
    assert "README.md" not in result

    # *.md should match only top-level markdown files
    result_md = glob_search("*.md", directory=str(temp_dir))
    assert "README.md" in result_md or any("README.md" in r for r in result_md.splitlines())
    assert "main.py" not in result_md

    # Pattern with no matches returns appropriate message
    result_none = glob_search("**/*.ts", directory=str(temp_dir))
    assert "No files matched" in result_none


def test_read_file_full(tmp_path):
    """Test that read_file returns numbered lines with [Lines 1-N of N] header."""
    content = "alpha\nbeta\ngamma\n"
    f = tmp_path / "sample.txt"
    f.write_text(content)

    result = read_file(str(f), start_line=1, end_line=0)

    lines = content.splitlines()
    n = len(lines)
    assert f"[Lines 1-{n} of {n}]" in result
    for i, line_text in enumerate(lines, start=1):
        assert f"{i:4d} | {line_text}" in result


def test_read_file_line_range(tmp_path):
    """Test that read_file with start_line=2, end_line=4 returns only those lines."""
    content = "line1\nline2\nline3\nline4\nline5\n"
    f = tmp_path / "multiline.txt"
    f.write_text(content)

    result = read_file(str(f), start_line=2, end_line=4)

    assert "line2" in result
    assert "line3" in result
    assert "line4" in result
    assert "line1" not in result
    assert "line5" not in result


def test_read_file_not_found(tmp_path):
    nonexistent = tmp_path / "does_not_exist.txt"
    result = read_file(str(nonexistent))
    assert "error" in result.lower() or "not found" in result.lower() or "no such" in result.lower()


def test_read_file_binary_detection(tmp_path):
    """Test that read_file detects binary files via null byte in first 1KB."""
    binary_file = tmp_path / "data.bin"
    binary_file.write_bytes(b"some text\x00more data")

    result = read_file(str(binary_file))

    assert "binary" in result.lower()


def test_read_file_truncation(tmp_path):
    """Test that read_file returns first chunk + outline when file exceeds max_size bytes."""
    large_file = tmp_path / "large.txt"
    max_size = 500
    # Write content larger than max_size (multiple lines so chunking works)
    large_file.write_text("\n".join(f"line {i}" for i in range(200)))

    result = read_file(str(large_file), max_size=max_size)

    assert "[Showing lines" in result
    assert "Remaining definitions" in result
    assert "To read more:" in result


def test_grep_search_with_context_lines(temp_dir):
    """Test that grep_search returns correct context lines when context_lines=2."""
    content = (
        "line one\n"
        "line two\n"
        "line three\n"
        "TARGET match here\n"
        "line five\n"
        "line six\n"
        "line seven\n"
    )
    target_file = temp_dir / "sample.txt"
    target_file.write_text(content)

    result = grep_search("TARGET", directory=str(temp_dir), context_lines=2)

    # The matched line should be marked with ">"
    assert "> TARGET match here" in result

    # Two lines before the match should appear
    assert "line two" in result
    assert "line three" in result

    # Two lines after the match should appear
    assert "line five" in result
    assert "line six" in result

    # Lines outside the context window should not appear
    assert "line one" not in result
    assert "line seven" not in result


def test_list_directory(temp_dir):
    """Test list_directory returns known files/subdirs and excludes .git hidden dir."""
    (temp_dir / "README.md").write_text("# readme")
    (temp_dir / "main.py").write_text("# main")
    sub = temp_dir / "src"
    sub.mkdir()
    (sub / "app.py").write_text("# app")
    hidden = temp_dir / ".git"
    hidden.mkdir()

    result = list_directory(str(temp_dir))

    assert "README.md" in result
    assert "main.py" in result
    assert "src/" in result
    assert ".git" not in result


def test_edit_file_basic(tmp_path):
    """Test that edit_file replaces a unique string and writes the updated content."""
    f = tmp_path / "greet.py"
    f.write_text("def hello():\n    return 'world'\n")

    set_skip_permissions(True)
    try:
        result = edit_file(str(f), "return 'world'", "return 'earth'")
    finally:
        set_skip_permissions(False)

    assert "Edited" in result
    assert f.read_text() == "def hello():\n    return 'earth'\n"


def test_edit_file_search_replace(tmp_path):
    """Test edit_file with a multi-line search/replace block on a temp file."""
    f = tmp_path / "config.py"
    original = (
        "DB_HOST = 'localhost'\n"
        "DB_PORT = 5432\n"
        "DB_NAME = 'mydb'\n"
        "DEBUG = True\n"
    )
    f.write_text(original)

    set_skip_permissions(True)
    try:
        result = edit_file(
            str(f),
            "DB_HOST = 'localhost'\nDB_PORT = 5432",
            "DB_HOST = 'production.example.com'\nDB_PORT = 5433",
        )
    finally:
        set_skip_permissions(False)

    assert "Edited" in result
    updated = f.read_text()
    assert "DB_HOST = 'production.example.com'" in updated
    assert "DB_PORT = 5433" in updated
    assert "DB_NAME = 'mydb'" in updated
    assert "DEBUG = True" in updated
    assert "localhost" not in updated


# ── Group 1: Pure Logic Functions ──────────────────────────────────


class TestShellSplit:
    def test_basic_and(self):
        result = _shell_split("ls && echo hello", ("&&",))
        assert result == ["ls", "echo hello"]

    def test_semicolon(self):
        result = _shell_split("cd /tmp; ls", (";",))
        assert result == ["cd /tmp", "ls"]

    def test_pipe(self):
        result = _shell_split("cat file | grep foo", ("|",))
        assert result == ["cat file", "grep foo"]

    def test_no_separator_returns_none(self):
        result = _shell_split("ls -la", ("&&",))
        assert result is None

    def test_quoted_separator_not_split(self):
        result = _shell_split('echo "a && b"', ("&&",))
        assert result is None


class TestBashPermissionResolve:
    """Tests for bash permission resolution (replaces TestIsSafeCommand)."""

    def setup_method(self):
        get_ctx().skip_permissions = False

    def test_safe_prefixes(self):
        for cmd in ["ls", "git status", "grep foo", "cat file.txt", "git log --oneline"]:
            action, _ = resolve_permission("bash", cmd)
            assert action == "allow", f"{cmd} should be allowed"

    def test_unsafe_commands(self):
        for cmd in ["rm -rf /", "pip install foo"]:
            action, _ = resolve_permission("bash", cmd)
            assert action == "ask", f"{cmd} should require asking"

    def test_chained_all_safe(self):
        action, _ = resolve_permission("bash", "ls && git status")
        assert action == "allow"

    def test_chained_mixed(self):
        action, _ = resolve_permission("bash", "ls && rm foo")
        assert action == "ask"

    def test_piped_all_safe(self):
        action, _ = resolve_permission("bash", "cat file | grep foo")
        assert action == "allow"

    def test_piped_mixed(self):
        action, _ = resolve_permission("bash", "cat file | python")
        assert action == "ask"


class TestIsLongRunning:
    def test_background_ampersand(self):
        assert _is_long_running("sleep 100 &") is True

    def test_server_patterns(self):
        for cmd in ["uvicorn app:main", "npm start", "flask run", "docker compose up"]:
            assert _is_long_running(cmd) is True, f"{cmd} should be long-running"

    def test_normal_commands(self):
        for cmd in ["ls", "git status", "python script.py"]:
            assert _is_long_running(cmd) is False, f"{cmd} should not be long-running"


class TestHtmlToText:
    def test_basic_paragraphs(self):
        result = _html_to_text("<p>Hello</p><p>World</p>")
        assert "Hello" in result
        assert "World" in result

    def test_strips_scripts(self):
        result = _html_to_text("<div>Visible</div><script>evil()</script>")
        assert "Visible" in result
        assert "evil" not in result

    def test_strips_style(self):
        result = _html_to_text("<h1>Title</h1><style>.cls{color:red}</style><p>Body</p>")
        assert "Title" in result
        assert "Body" in result
        assert "color" not in result

    def test_empty_input(self):
        assert _html_to_text("") == ""


# ── Group 2: get_project_tree ──────────────────────────────────────


class TestGetProjectTree:
    def test_basic_tree(self, tmp_path):
        (tmp_path / "README.md").write_text("# readme")
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("# main")

        result = get_project_tree(str(tmp_path))
        assert "src/" in result or "src" in result
        assert "main.py" in result
        assert "README.md" in result

    def test_max_depth(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        (deep / "deep.txt").write_text("deep")

        result = get_project_tree(str(tmp_path), max_depth=2)
        # Level 3+ should not appear
        assert "deep.txt" not in result

    def test_max_files_per_dir(self, tmp_path):
        for i in range(35):
            (tmp_path / f"file_{i:02d}.txt").write_text(f"content {i}")

        result = get_project_tree(str(tmp_path), max_files_per_dir=10)
        assert "more files" in result

    def test_nonexistent_path(self, tmp_path):
        result = get_project_tree(str(tmp_path / "does_not_exist"))
        assert result == ""


# ── Group 3: edit_file Error Cases + edit_files ────────────────────


class TestEditFileErrors:
    def test_old_string_not_found(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("hello world")

        set_skip_permissions(True)
        try:
            result = edit_file(str(f), "NONEXISTENT", "replacement")
        finally:
            set_skip_permissions(False)

        assert "not found" in result.lower()

    def test_old_string_multiple_occurrences(self, tmp_path):
        f = tmp_path / "test.py"
        f.write_text("foo bar foo baz foo")

        set_skip_permissions(True)
        try:
            result = edit_file(str(f), "foo", "qux")
        finally:
            set_skip_permissions(False)

        assert "3 times" in result

    def test_file_not_found(self, tmp_path):
        set_skip_permissions(True)
        try:
            result = edit_file(str(tmp_path / "nonexistent.py"), "old", "new")
        finally:
            set_skip_permissions(False)

        assert "not found" in result.lower()


# ── Group 4: Cache and Callbacks ───────────────────────────────────


class TestCacheAndCallbacks:
    def test_read_file_cache_hit(self, tmp_path):
        f = tmp_path / "cached.txt"
        f.write_text("line1\nline2\nline3\nline4\n")

        try:
            first = read_file(str(f), start_line=1, end_line=3)
            assert "[cached]" not in first

            second = read_file(str(f), start_line=1, end_line=3)
            assert "[cached]" in second
        finally:
            clear_read_cache()

    def test_clear_read_cache(self, tmp_path):
        f = tmp_path / "cached2.txt"
        f.write_text("content\n")

        try:
            read_file(str(f), start_line=1, end_line=1)
            assert len(get_ctx().read_cache) > 0

            clear_read_cache()
            assert len(get_ctx().read_cache) == 0
        finally:
            clear_read_cache()

    def test_set_on_file_mutation_callback(self, tmp_path):
        calls = []

        def on_mutation():
            calls.append(True)

        ctx = get_ctx()
        ctx.on_file_mutation = on_mutation
        try:
            target = tmp_path / "mutated.txt"
            write_file(str(target), "content")

            assert len(calls) > 0, "Mutation callback should have been invoked"
        finally:
            ctx.on_file_mutation = None

    def test_reset_session(self):
        ctx = get_ctx()
        ctx.session_allowed.add(("edit", "*"))
        assert ("edit", "*") in ctx.session_allowed

        reset_session()
        assert len(ctx.session_allowed) == 0


class TestSkipPermissions:
    """Tests for set_skip_permissions / get_skip_permissions."""

    def test_default_is_false(self):
        """Initially skip_permissions is False."""
        set_skip_permissions(False)
        assert get_skip_permissions() is False

    def test_set_true_and_read_back(self):
        """Setting to True is reflected immediately by get_skip_permissions."""
        original = get_skip_permissions()
        try:
            set_skip_permissions(True)
            assert get_skip_permissions() is True
        finally:
            set_skip_permissions(original)

    def test_set_false_and_read_back(self):
        """Setting back to False is also reflected immediately."""
        set_skip_permissions(True)
        try:
            set_skip_permissions(False)
            assert get_skip_permissions() is False
        finally:
            set_skip_permissions(False)


class TestFormatUnifiedDiff:
    """Tests for _format_unified_diff — unified diff against full file contents."""

    @staticmethod
    def _render(group) -> str:
        return "\n".join(str(r) for r in group.renderables)

    def test_no_changes(self):
        group = _format_unified_diff("same\ntext\n", "same\ntext\n", "f.txt")
        rendered = self._render(group)
        assert "(no changes)" in rendered

    def test_pure_addition_new_file(self):
        """Empty old_content (new file creation) shows all lines as additions."""
        group = _format_unified_diff("", "alpha\nbeta\ngamma\n", "new.txt")
        rendered = self._render(group)
        assert "+ alpha" in rendered
        assert "+ beta" in rendered
        assert "+ gamma" in rendered
        assert "- " not in rendered
        assert "+3" in rendered and "-0" in rendered

    def test_pure_deletion(self):
        group = _format_unified_diff("doomed\nlines\n", "", "old.txt")
        rendered = self._render(group)
        assert "- doomed" in rendered
        assert "- lines" in rendered
        assert "+0" in rendered and "-2" in rendered

    def test_hunk_header_present(self):
        """Every non-empty diff contains a @@ hunk header."""
        group = _format_unified_diff("a\nb\nc\n", "a\nB\nc\n", "f.txt")
        rendered = self._render(group)
        assert "@@" in rendered

    def test_single_line_change_preserves_context(self):
        """A single-line change surrounded by unchanged lines shows the context."""
        old = "\n".join(f"line{i}" for i in range(1, 11))
        new_lines = [f"line{i}" for i in range(1, 11)]
        new_lines[4] = "LINE5_CHANGED"
        new = "\n".join(new_lines)

        group = _format_unified_diff(old, new, "sample.txt")
        rendered = self._render(group)

        # The unchanged neighbors must appear as context lines (not stripped)
        assert "line3" in rendered
        assert "line4" in rendered
        assert "line6" in rendered
        assert "line7" in rendered
        # The change itself
        assert "- line5" in rendered
        assert "+ LINE5_CHANGED" in rendered
        assert "+1" in rendered and "-1" in rendered

    def test_line_numbers_rendered(self):
        """Gutter shows line numbers for the first hunk."""
        group = _format_unified_diff("a\nb\nc\n", "a\nB\nc\n", "f.txt")
        rendered = self._render(group)
        # Old line 2 and new line 2 should both appear in the gutter
        assert "2" in rendered

    def test_large_diff_is_truncated(self):
        """Very large diffs show a truncation hint."""
        old = "\n".join(f"old{i}" for i in range(500))
        new = "\n".join(f"new{i}" for i in range(500))
        group = _format_unified_diff(old, new, "big.txt", max_total_lines=30)
        rendered = self._render(group)
        assert "more diff lines" in rendered

    def test_file_path_header(self):
        group = _format_unified_diff("a\n", "b\n", "path/to/file.py")
        rendered = self._render(group)
        assert "path/to/file.py" in rendered


class TestResolveTools:
    """Test resolve_tools function."""

    def test_empty_returns_general_tools(self):
        result = resolve_tools([])
        assert result == list(GENERAL_TOOLS)

    def test_allowlist(self):
        result = resolve_tools(["read_file", "bash"])
        assert len(result) == 2
        assert all(f.__name__ in ("read_file", "bash") for f in result)

    def test_dict_disable(self):
        result = resolve_tools({"bash": False})
        names = [f.__name__ for f in result]
        assert "bash" not in names
        assert "read_file" in names  # other tools still present

    def test_dict_enable_extra(self):
        result = resolve_tools({"rank_files": True})
        names = [f.__name__ for f in result]
        assert "rank_files" in names

    def test_unknown_tool_ignored(self):
        result = resolve_tools(["read_file", "nonexistent_tool"])
        assert len(result) == 1
        assert result[0].__name__ == "read_file"

    def test_registry_has_core_tools(self):
        for name in ("read_file", "write_file", "edit_file", "bash",
                      "glob_search", "grep_search", "delegate_task"):
            assert name in TOOL_REGISTRY


class TestDelegateTaskDocstring:
    """Tests for dynamic delegate_task docstring with available subagents."""

    def test_docstring_includes_subagents(self):
        from aru.config import CustomAgent
        agents = {
            "reviewer": CustomAgent(
                name="Reviewer", description="Review code for quality",
                system_prompt="p", source_path="/f.md", mode="subagent",
            ),
            "primary_agent": CustomAgent(
                name="Primary", description="Primary agent",
                system_prompt="p", source_path="/f.md", mode="primary",
            ),
        }
        set_custom_agents(agents)
        doc = delegate_task.__doc__
        assert 'agent_name="reviewer"' in doc
        assert "Review code for quality" in doc
        # Primary agents should not be listed (only subagents are registered)
        assert "Primary" not in doc

    def test_docstring_without_subagents(self):
        set_custom_agents({})
        doc = delegate_task.__doc__
        assert "Available specialized agents" not in doc
        assert "delegate" in doc.lower()