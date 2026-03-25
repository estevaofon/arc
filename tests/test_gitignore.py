"""Unit tests for aru/tools/gitignore.py"""

import os
import time
from pathlib import Path

import pytest

from aru.tools.gitignore import (
    _FALLBACK_PATTERNS,
    _cache,
    _find_git_root,
    is_ignored,
    load_gitignore,
    walk_filtered,
)


@pytest.fixture
def temp_project(tmp_path):
    """Create a temporary project structure."""
    project = tmp_path / "project"
    project.mkdir()
    return project


@pytest.fixture
def git_project(temp_project):
    """Create a project with .git directory."""
    git_dir = temp_project / ".git"
    git_dir.mkdir()
    return temp_project


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear gitignore cache before each test."""
    _cache.clear()
    yield
    _cache.clear()


class TestFindGitRoot:
    """Tests for _find_git_root()."""

    def test_finds_git_root(self, git_project):
        """Should find .git directory in parent."""
        subdir = git_project / "src" / "nested"
        subdir.mkdir(parents=True)
        
        root = _find_git_root(str(subdir))
        assert root == str(git_project)

    def test_returns_none_when_no_git(self, temp_project):
        """Should return None when no .git found."""
        root = _find_git_root(str(temp_project))
        assert root is None

    def test_finds_git_in_current_dir(self, git_project):
        """Should find .git in the starting directory."""
        root = _find_git_root(str(git_project))
        assert root == str(git_project)


class TestLoadGitignore:
    """Tests for load_gitignore()."""

    def test_loads_fallback_patterns(self, temp_project):
        """Should include hardcoded fallback patterns."""
        spec = load_gitignore(str(temp_project))
        
        # Test fallback patterns are applied
        assert spec.match_file("__pycache__/")
        assert spec.match_file("node_modules/")
        assert spec.match_file(".git/")
        assert spec.match_file(".aru/")
        assert spec.match_file("test.pyc")

    def test_loads_gitignore_file(self, temp_project):
        """Should parse .gitignore patterns."""
        gitignore = temp_project / ".gitignore"
        gitignore.write_text("*.log\nbuild/\n# comment\n\n")
        
        spec = load_gitignore(str(temp_project))
        
        assert spec.match_file("debug.log")
        assert spec.match_file("build/")

    def test_caches_by_root_and_mtime(self, temp_project):
        """Should cache results keyed by (root, mtime)."""
        gitignore = temp_project / ".gitignore"
        gitignore.write_text("*.tmp")
        
        spec1 = load_gitignore(str(temp_project))
        spec2 = load_gitignore(str(temp_project))
        
        # Should return same cached object
        assert spec1 is spec2
        assert len(_cache) == 1

    def test_invalidates_cache_on_gitignore_change(self, temp_project):
        """Should reload when .gitignore is modified."""
        gitignore = temp_project / ".gitignore"
        gitignore.write_text("*.tmp")
        
        spec1 = load_gitignore(str(temp_project))
        assert spec1.match_file("test.tmp")
        
        # Modify gitignore
        time.sleep(0.01)  # Ensure mtime changes
        gitignore.write_text("*.log")
        
        spec2 = load_gitignore(str(temp_project))
        
        # Should be different spec
        assert spec2.match_file("test.log")
        # Old cache entry should be cleared
        assert len(_cache) == 1

    def test_handles_missing_gitignore(self, temp_project):
        """Should work without .gitignore file."""
        spec = load_gitignore(str(temp_project))
        
        # Should still have fallback patterns
        assert spec.match_file("__pycache__/")

    def test_ignores_comments_and_blanks(self, temp_project):
        """Should skip comments and blank lines."""
        gitignore = temp_project / ".gitignore"
        gitignore.write_text("# This is a comment\n\n*.log\n  \n# Another comment")
        
        spec = load_gitignore(str(temp_project))
        
        assert spec.match_file("test.log")
        assert not spec.match_file("# This is a comment")


class TestIsIgnored:
    """Tests for is_ignored()."""

    def test_checks_fallback_patterns(self, temp_project):
        """Should detect fallback patterns."""
        assert is_ignored("__pycache__/file.py", str(temp_project))
        assert is_ignored("node_modules/package/index.js", str(temp_project))
        assert is_ignored("test.pyc", str(temp_project))

    def test_checks_gitignore_patterns(self, temp_project):
        """Should detect .gitignore patterns."""
        gitignore = temp_project / ".gitignore"
        gitignore.write_text("*.log\ndist/")
        
        assert is_ignored("app.log", str(temp_project))
        assert is_ignored("dist/bundle.js", str(temp_project))
        assert not is_ignored("app.py", str(temp_project))

    def test_normalizes_backslashes(self, temp_project):
        """Should handle Windows-style paths."""
        gitignore = temp_project / ".gitignore"
        gitignore.write_text("build/")
        
        # Windows-style path should still match
        assert is_ignored("build\\output.txt", str(temp_project))

    def test_relative_path_matching(self, temp_project):
        """Should match relative paths correctly."""
        gitignore = temp_project / ".gitignore"
        gitignore.write_text("*.tmp")
        
        assert is_ignored("file.tmp", str(temp_project))
        assert is_ignored("subdir/file.tmp", str(temp_project))


class TestWalkFiltered:
    """Tests for walk_filtered()."""

    def test_filters_ignored_files(self, temp_project):
        """Should exclude gitignored files."""
        (temp_project / ".gitignore").write_text("*.log")
        (temp_project / "keep.txt").write_text("ok")
        (temp_project / "remove.log").write_text("ignore")
        
        results = list(walk_filtered(str(temp_project)))
        
        assert len(results) == 1
        dirpath, dirs, files = results[0]
        assert "keep.txt" in files
        assert "remove.log" not in files
        assert ".gitignore" in files

    def test_filters_ignored_directories(self, temp_project):
        """Should not descend into gitignored directories."""
        (temp_project / ".gitignore").write_text("build/")
        build = temp_project / "build"
        build.mkdir()
        (build / "output.txt").write_text("data")
        
        src = temp_project / "src"
        src.mkdir()
        (src / "main.py").write_text("code")
        
        results = list(walk_filtered(str(temp_project)))
        dirpaths = [r[0] for r in results]
        
        # Should visit src but not build
        assert str(src) in dirpaths
        assert str(build) not in dirpaths

    def test_filters_fallback_patterns(self, temp_project):
        """Should exclude fallback patterns."""
        pycache = temp_project / "__pycache__"
        pycache.mkdir()
        (pycache / "module.pyc").write_text("")
        
        (temp_project / "main.py").write_text("code")
        
        results = list(walk_filtered(str(temp_project)))
        dirpaths = [r[0] for r in results]
        
        # Should not descend into __pycache__
        assert str(pycache) not in dirpaths
        
        # Should include main.py
        files = results[0][2]
        assert "main.py" in files

    def test_uses_git_root_for_patterns(self, git_project):
        """Should use git root for pattern matching."""
        (git_project / ".gitignore").write_text("*.tmp")
        
        subdir = git_project / "nested" / "deep"
        subdir.mkdir(parents=True)
        (subdir / "file.tmp").write_text("")
        (subdir / "file.py").write_text("")
        
        results = list(walk_filtered(str(subdir)))
        
        # Should respect .gitignore from git root
        files = []
        for _, _, fs in results:
            files.extend(fs)
        
        assert "file.py" in files
        assert "file.tmp" not in files

    def test_fallback_to_directory_when_no_git(self, temp_project):
        """Should use directory itself when no git root found."""
        (temp_project / ".gitignore").write_text("*.bak")
        (temp_project / "file.txt").write_text("")
        (temp_project / "file.bak").write_text("")
        
        results = list(walk_filtered(str(temp_project)))
        files = results[0][2]
        
        assert "file.txt" in files
        assert "file.bak" not in files

    def test_yields_os_walk_compatible_tuples(self, temp_project):
        """Should yield (dirpath, dirs, files) tuples like os.walk."""
        subdir = temp_project / "sub"
        subdir.mkdir()
        (temp_project / "root.txt").write_text("")
        (subdir / "nested.txt").write_text("")
        
        results = list(walk_filtered(str(temp_project)))
        
        # Should have 2 levels
        assert len(results) == 2
        
        for dirpath, dirs, files in results:
            assert isinstance(dirpath, str)
            assert isinstance(dirs, list)
            assert isinstance(files, list)

    def test_excludes_aru_directory(self, temp_project):
        """Should always exclude .aru directory (fallback pattern)."""
        aru_dir = temp_project / ".aru"
        aru_dir.mkdir()
        (aru_dir / "sessions").mkdir()
        (aru_dir / "sessions" / "data.json").write_text("{}")

        results = list(walk_filtered(str(temp_project)))
        dirpaths = [r[0] for r in results]

        # .aru should never be visited
        assert not any(".aru" in dp for dp in dirpaths)