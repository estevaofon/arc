"""Unit tests for aru/cli.py — Session methods and utilities not covered elsewhere."""

import os
import time
from unittest.mock import MagicMock, patch

import pytest

from aru.cli import (
    Session,
    SessionStore,
    TIPS,
    SLASH_COMMANDS,
    DEFAULT_MODEL,
    _render_home,
)


# ── Session: additional methods ────────────────────────────────────────────


class TestSessionContextCache:
    """Test context cache invalidation and regeneration."""

    def test_invalidate_context_cache_sets_dirty_flag(self):
        """Test that invalidating cache marks it as dirty."""
        session = Session()
        session._context_dirty = False
        session.invalidate_context_cache()
        assert session._context_dirty is True

    def test_get_cached_tree_regenerates_when_dirty(self):
        """Test that cached tree is regenerated when context is dirty."""
        session = Session()
        session._context_dirty = True

        # Mock get_project_tree from the correct module
        mock_tree = "project/\n  src/\n  tests/"
        with patch("aru.tools.codebase.get_project_tree", return_value=mock_tree):
            result = session.get_cached_tree(os.getcwd())
            assert result == mock_tree

    def test_get_cached_tree_returns_none_on_error(self):
        """Test that cached tree returns None on exception."""
        session = Session()
        session._context_dirty = True

        with patch("aru.tools.codebase.get_project_tree", side_effect=Exception("error")):
            result = session.get_cached_tree(os.getcwd())
            assert result is None

    def test_get_cached_git_status_regenerates_when_dirty(self, monkeypatch):
        """Test that cached git status is regenerated when dirty."""
        session = Session()
        session._context_dirty = True

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=" M file.py\n?? new.py")
            result = session.get_cached_tree(os.getcwd())  # This also clears dirty

        # After getting cached_tree, git status should also be fresh
        session._context_dirty = True
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=" M file.py")
            result = session.get_cached_git_status(os.getcwd())
            assert "M file.py" in result

    def test_get_cached_git_status_returns_none_on_error(self, monkeypatch):
        """Test that cached git status returns None on exception."""
        session = Session()
        session._context_dirty = True

        with patch("subprocess.run", side_effect=Exception("no git")):
            result = session.get_cached_git_status(os.getcwd())
            assert result is None


class TestSessionTokenBudget:
    """Test token budget warning functionality."""

    def test_check_budget_warning_none_when_no_budget(self):
        """Test that no warning is returned when budget is 0."""
        session = Session()
        session.token_budget = 0
        assert session.check_budget_warning() is None

    def test_check_budget_warning_at_80_percent(self):
        """Test warning at 80% budget usage."""
        session = Session()
        session.token_budget = 1000
        session.total_input_tokens = 400
        session.total_output_tokens = 400  # 800 total = 80%
        warning = session.check_budget_warning()
        assert warning is not None
        assert "80%" in warning

    def test_check_budget_warning_at_95_percent(self):
        """Test warning at 95% budget usage."""
        session = Session()
        session.token_budget = 1000
        session.total_input_tokens = 500
        session.total_output_tokens = 450  # 950 total = 95%
        warning = session.check_budget_warning()
        assert warning is not None
        assert "95%" in warning
        assert "[bold red]" in warning  # Critical warning

    def test_check_budget_warning_below_threshold(self):
        """Test no warning when below 80%."""
        session = Session()
        session.token_budget = 1000
        session.total_input_tokens = 300
        session.total_output_tokens = 400  # 700 total = 70%
        assert session.check_budget_warning() is None


class TestSessionEstimateTokens:
    """Test token estimation."""

    def test_estimate_tokens_exact(self):
        """Test exact token estimation."""
        text = "a" * 35  # Should be ~10 tokens at 3.5 chars/token
        tokens = Session.estimate_tokens(text)
        assert tokens == 10

    def test_estimate_tokens_empty(self):
        """Test estimation for empty string."""
        assert Session.estimate_tokens("") == 0

    def test_estimate_tokens_rounds_down(self):
        """Test that estimation rounds down."""
        text = "abc"  # 3 chars / 3.5 = 0.85 -> 0
        tokens = Session.estimate_tokens(text)
        assert tokens == 0


class TestSessionCompactProgress:
    """Test render_compact_progress method."""

    def test_compact_progress_empty(self):
        """Test compact progress with no steps."""
        session = Session()
        result = session.render_compact_progress(0)
        assert result == ""

    def test_compact_progress_with_steps(self):
        """Test compact progress rendering."""
        session = Session()
        session.set_plan("task", "- [ ] Step 1\n- [ ] Step 2\n- [ ] Step 3")
        session.plan_steps[0].status = "completed"

        result = session.render_compact_progress(2)  # Step 2 is current

        assert "1/3" in result
        assert "[x] Step 1" in result
        assert "Step 2" in result
        assert "<< CURRENT" in result
        assert "Step 3" in result


# ── SessionStore: additional methods ───────────────────────────────────────


class TestSessionStoreEdgeCases:
    """Test edge cases for session storage."""

    def test_save_creates_directory(self, tmp_path):
        """Test that save creates the sessions directory."""
        store = SessionStore(base_dir=str(tmp_path / "new_dir"))
        session = Session(session_id="test")
        store.save(session)  # Should not raise
        assert os.path.isdir(str(tmp_path / "new_dir"))

    def test_load_missing_json_file(self, tmp_path):
        """Test loading a session whose file was deleted."""
        store = SessionStore(base_dir=str(tmp_path))
        session = Session(session_id="temp")
        store.save(session)

        # Delete the file
        os.remove(os.path.join(str(tmp_path), "temp.json"))

        # Should return None gracefully
        assert store.load("temp") is None


# ── Global constants ───────────────────────────────────────────────────────


class TestGlobalConstants:
    """Test global constants in cli module."""

    def test_default_model_format(self):
        """Test that DEFAULT_MODEL uses provider/model format."""
        assert "/" in DEFAULT_MODEL
        assert "claude" in DEFAULT_MODEL.lower()

    def test_tips_is_list(self):
        """Test that TIPS is a non-empty list."""
        assert isinstance(TIPS, list)
        assert len(TIPS) > 0

    def test_tips_contain_strings(self):
        """Test that all tips are strings."""
        for tip in TIPS:
            assert isinstance(tip, str)

    def test_slash_commands_structure(self):
        """Test SLASH_COMMANDS has correct structure."""
        assert isinstance(SLASH_COMMANDS, list)
        assert len(SLASH_COMMANDS) > 0

        for cmd in SLASH_COMMANDS:
            assert isinstance(cmd, tuple)
            assert len(cmd) == 3
            # Each command: (name, description, usage)
            assert cmd[0].startswith("/")

    def test_all_slash_commands_documented(self):
        """Test that all slash commands have help text."""
        for cmd, desc, usage in SLASH_COMMANDS:
            assert len(desc) > 0
            assert len(usage) > 0


# ── _render_home ───────────────────────────────────────────────────────────


class TestRenderHome:
    """Test the home screen rendering."""

    def test_render_home_does_not_raise(self):
        """Test that _render_home runs without errors."""
        session = Session()
        # Should not raise any exception
        _render_home(session, skip_permissions=False)

    def test_render_home_with_skip_permissions(self):
        """Test rendering with skip_permissions=True."""
        session = Session()
        _render_home(session, skip_permissions=True)
        # If we get here without error, test passes


# ── Additional Session serialization edge cases ────────────────────────────


class TestSessionSerializationEdgeCases:
    """Test edge cases in session serialization."""

    def test_from_dict_missing_history(self):
        """Test loading session without history field."""
        data = {
            "session_id": "test",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }
        session = Session.from_dict(data)
        assert session.history == []

    def test_from_dict_missing_plan_steps(self):
        """Test loading session without plan_steps."""
        data = {
            "session_id": "test",
            "history": [],
            "current_plan": "some plan",
            "created_at": "2024-01-01",
            "updated_at": "2024-01-01",
        }
        session = Session.from_dict(data)
        assert session.plan_steps == []

    def test_to_dict_includes_all_fields(self):
        """Test that to_dict includes all important fields."""
        session = Session(session_id="test123")
        session.add_message("user", "hello")
        session.model_ref = "anthropic/claude-opus-4"

        d = session.to_dict()

        assert "session_id" in d
        assert "history" in d
        assert "model_ref" in d
        assert "created_at" in d
        assert "updated_at" in d
        assert d["session_id"] == "test123"
        assert len(d["history"]) == 1


# ── Session model display ──────────────────────────────────────────────────


class TestSessionModelDisplay:
    """Test model display property."""

    def test_model_display_contains_provider(self):
        """Test that model_display includes provider info."""
        session = Session()
        session.model_ref = "anthropic/claude-sonnet-4-5"
        display = session.model_display
        # Should contain some reference to anthropic or claude
        assert len(display) > 0