"""Tests for the `invoked_skills` skill-preservation feature.

Covers four areas:

1. `Session.record_invoked_skill` state + serialization round-trip.
2. `Skill.reminder` frontmatter parsing.
3. `_build_skills_preservation_item` — the post-compact `<system-reminder>`
   block (budget, per-skill truncation, sort order, empty-input handling).
4. `apply_compaction` end-to-end: the preservation item is injected right
   after the summary marker when invoked_skills is non-empty.
"""

from __future__ import annotations

import time

import pytest

from aru.config import Skill, _parse_skill_metadata, _discover_skills
from aru.context import (
    POST_COMPACT_MAX_CHARS_PER_SKILL,
    POST_COMPACT_SKILLS_BUDGET_CHARS,
    SKILL_TRUNCATION_MARKER,
    _build_skills_preservation_item,
    apply_compaction,
)
from aru.history_blocks import text_block
from aru.session import InvokedSkill, Session


# ── Session.record_invoked_skill ──────────────────────────────────


class TestRecordInvokedSkill:
    def test_records_name_content_and_path(self):
        session = Session()
        session.record_invoked_skill(
            "brainstorming",
            "# Body\nRules here",
            source_path="/fake/brainstorming/SKILL.md",
        )

        assert "brainstorming" in session.invoked_skills
        entry = session.invoked_skills["brainstorming"]
        assert entry.name == "brainstorming"
        assert entry.content == "# Body\nRules here"
        assert entry.source_path == "/fake/brainstorming/SKILL.md"
        assert entry.invoked_at > 0

    def test_re_invocation_overwrites_content(self):
        session = Session()
        session.record_invoked_skill("s", "v1", source_path="p")
        old_ts = session.invoked_skills["s"].invoked_at
        # Advance the monotonic clock enough for time.time() to tick
        time.sleep(0.01)
        session.record_invoked_skill("s", "v2", source_path="p2")

        entry = session.invoked_skills["s"]
        assert entry.content == "v2"
        assert entry.source_path == "p2"
        assert entry.invoked_at >= old_ts

    def test_empty_name_noop(self):
        session = Session()
        session.record_invoked_skill("", "body")
        assert session.invoked_skills == {}

    def test_multiple_skills_accumulate(self):
        session = Session()
        session.record_invoked_skill("a", "body-a")
        session.record_invoked_skill("b", "body-b")
        session.record_invoked_skill("c", "body-c")

        assert set(session.invoked_skills) == {"a", "b", "c"}


class TestInvokedSkillSerialization:
    def test_roundtrip_via_session_to_and_from_dict(self):
        session = Session()
        session.record_invoked_skill("brainstorming", "body-1", "/p/1")
        session.record_invoked_skill("writing-plans", "body-2", "/p/2")

        dumped = session.to_dict()
        assert "invoked_skills" in dumped
        assert set(dumped["invoked_skills"]) == {"brainstorming", "writing-plans"}

        restored = Session.from_dict(dumped)
        assert set(restored.invoked_skills) == {"brainstorming", "writing-plans"}
        b = restored.invoked_skills["brainstorming"]
        assert b.content == "body-1"
        assert b.source_path == "/p/1"
        assert isinstance(b, InvokedSkill)

    def test_from_dict_tolerates_missing_field(self):
        """Old sessions (pre-feature) won't have invoked_skills in JSON."""
        session = Session.from_dict({
            "session_id": "x",
            "history": [],
        })
        assert session.invoked_skills == {}

    def test_active_skill_survives_roundtrip(self):
        session = Session()
        session.active_skill = "brainstorming"
        restored = Session.from_dict(session.to_dict())
        assert restored.active_skill == "brainstorming"


# ── Skill.reminder frontmatter ────────────────────────────────────


class TestSkillReminderFrontmatter:
    def test_parses_reminder_string(self):
        meta = _parse_skill_metadata({
            "name": "brainstorming",
            "description": "...",
            "reminder": "Do NOT write spec before 5 section approvals.",
        })
        assert meta["reminder"] == "Do NOT write spec before 5 section approvals."

    def test_missing_reminder_defaults_empty(self):
        meta = _parse_skill_metadata({
            "name": "x",
            "description": "...",
        })
        assert meta["reminder"] == ""

    def test_blank_reminder_normalizes_to_empty(self):
        meta = _parse_skill_metadata({
            "name": "x",
            "description": "...",
            "reminder": "   ",
        })
        assert meta["reminder"] == ""

    def test_discover_skills_populates_reminder(self, tmp_path):
        skills_dir = tmp_path / "skills" / "brainstorming"
        skills_dir.mkdir(parents=True)
        (skills_dir / "SKILL.md").write_text(
            "---\n"
            "name: brainstorming\n"
            "description: Test\n"
            "reminder: short reminder text\n"
            "---\n"
            "body"
        )
        skills = _discover_skills([tmp_path])
        assert "brainstorming" in skills
        assert skills["brainstorming"].reminder == "short reminder text"

    def test_skill_dataclass_default_reminder_empty(self):
        s = Skill(name="x", description="d", content="b", source_path="/p")
        assert s.reminder == ""


# ── _build_skills_preservation_item ───────────────────────────────


def _invoked(name: str, content: str, *, invoked_at: float, source_path: str = "/p") -> InvokedSkill:
    return InvokedSkill(name=name, content=content, source_path=source_path, invoked_at=invoked_at)


class TestBuildSkillsPreservationItem:
    def test_none_input_returns_none(self):
        assert _build_skills_preservation_item(None) is None

    def test_empty_input_returns_none(self):
        assert _build_skills_preservation_item({}) is None

    def test_all_blank_bodies_returns_none(self):
        skills = {
            "a": _invoked("a", "", invoked_at=1.0),
            "b": _invoked("b", "   ", invoked_at=2.0),
        }
        assert _build_skills_preservation_item(skills) is None

    def test_wraps_in_system_reminder(self):
        skills = {"brainstorming": _invoked("brainstorming", "Rule X", invoked_at=1.0)}
        item = _build_skills_preservation_item(skills)

        assert item is not None
        assert item["role"] == "user"
        text = item["content"][0]["text"]
        assert text.startswith("<system-reminder>")
        assert text.rstrip().endswith("</system-reminder>")
        assert "### Skill: /brainstorming" in text
        assert "Rule X" in text

    def test_most_recent_first(self):
        skills = {
            "old": _invoked("old", "OLD-BODY", invoked_at=1.0),
            "new": _invoked("new", "NEW-BODY", invoked_at=100.0),
            "mid": _invoked("mid", "MID-BODY", invoked_at=50.0),
        }
        text = _build_skills_preservation_item(skills)["content"][0]["text"]
        # Check order by index of each body in the rendered text
        i_new = text.index("NEW-BODY")
        i_mid = text.index("MID-BODY")
        i_old = text.index("OLD-BODY")
        assert i_new < i_mid < i_old

    def test_per_skill_head_keep_truncation(self):
        big = "X" * (POST_COMPACT_MAX_CHARS_PER_SKILL + 500)
        skills = {"big": _invoked("big", big, invoked_at=1.0)}
        text = _build_skills_preservation_item(skills)["content"][0]["text"]

        # Body must be truncated — full content cannot fit
        assert big not in text
        assert SKILL_TRUNCATION_MARKER.strip() in text
        # Head of the body should be preserved (first 100 chars of X's)
        assert "X" * 100 in text

    def test_budget_drops_oldest_skill(self, monkeypatch):
        """When enough skills are queued to overflow the budget, the oldest
        ones are dropped so the block stays within `POST_COMPACT_SKILLS_BUDGET_CHARS`.
        Shrinks both caps for the test so we can reason about exact sizes."""
        from aru import context as ctx_mod
        monkeypatch.setattr(ctx_mod, "POST_COMPACT_MAX_CHARS_PER_SKILL", 100)
        monkeypatch.setattr(ctx_mod, "POST_COMPACT_SKILLS_BUDGET_CHARS", 250)

        body = "Y" * 100  # each skill is exactly at the per-skill cap
        skills = {
            "old":    _invoked("old",    body, invoked_at=1.0),
            "newer":  _invoked("newer",  body, invoked_at=2.0),
            "newest": _invoked("newest", body, invoked_at=3.0),
        }
        text = _build_skills_preservation_item(skills)["content"][0]["text"]

        assert "### Skill: /newest" in text
        # 3 × 100 = 300 exceeds budget of 250; at least one must be dropped.
        dropped = (
            "### Skill: /old" not in text or "### Skill: /newer" not in text
        )
        assert dropped

    def test_source_path_rendered_when_present(self):
        skills = {"b": _invoked("b", "body", invoked_at=1.0, source_path="/abs/path/SKILL.md")}
        text = _build_skills_preservation_item(skills)["content"][0]["text"]
        assert "Path: /abs/path/SKILL.md" in text

    def test_source_path_omitted_when_empty(self):
        skills = {"b": _invoked("b", "body", invoked_at=1.0, source_path="")}
        text = _build_skills_preservation_item(skills)["content"][0]["text"]
        assert "Path:" not in text

    def test_accepts_dict_shaped_entries(self):
        """Post-compact callers may hand us dicts (e.g. from an external cache)
        rather than InvokedSkill instances — the helper tolerates both."""
        skills = {
            "b": {
                "name": "b",
                "content": "dict-body",
                "source_path": "/p",
                "invoked_at": 1.0,
            }
        }
        text = _build_skills_preservation_item(skills)["content"][0]["text"]
        assert "dict-body" in text


# ── apply_compaction integration ─────────────────────────────────


def _user(text: str) -> dict:
    return {"role": "user", "content": [text_block(text)]}


def _assistant(text: str) -> dict:
    return {"role": "assistant", "content": [text_block(text)]}


class TestApplyCompactionWithSkills:
    def test_preservation_injected_after_summary(self):
        history = [_user("old 1"), _assistant("old 2")] * 50 + [_user("recent")]
        skills = {"brainstorming": _invoked("brainstorming", "Do NOT skip Step 4", invoked_at=1.0)}

        result = apply_compaction(history, "summary text", invoked_skills=skills)

        # Layout: user(please summarize), assistant(summary, summary=True), user(preservation), ...recent
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[1].get("summary") is True

        preserved = result[2]
        assert preserved["role"] == "user"
        preserved_text = preserved["content"][0]["text"]
        assert preserved_text.startswith("<system-reminder>")
        assert "Do NOT skip Step 4" in preserved_text

    def test_no_preservation_when_skills_empty(self):
        history = [_user("old"), _assistant("reply"), _user("recent")]
        result = apply_compaction(history, "summary text", invoked_skills=None)

        # Without skills, item at index 2 should be recent content, not a reminder
        assert result[1].get("summary") is True
        if len(result) > 2:
            third_text = result[2]["content"][0]["text"] if result[2]["content"] else ""
            assert "<system-reminder>" not in third_text

    def test_preservation_comes_before_recent_history(self):
        history = [_user("ancient")] * 40 + [_user("recent-A"), _assistant("recent-B")]
        skills = {"b": _invoked("b", "skill body", invoked_at=1.0)}

        result = apply_compaction(history, "summary", invoked_skills=skills)

        # Find index of preservation reminder
        reminder_idx = None
        for i, msg in enumerate(result):
            if msg["role"] == "user" and msg["content"]:
                text = msg["content"][0].get("text", "")
                if text.startswith("<system-reminder>"):
                    reminder_idx = i
                    break
        assert reminder_idx is not None

        # After the reminder, we expect the recent message(s)
        after_reminder = result[reminder_idx + 1 :]
        combined = " ".join(
            (m["content"][0].get("text", "") if m["content"] else "") for m in after_reminder
        )
        assert "recent-A" in combined or "recent-B" in combined
