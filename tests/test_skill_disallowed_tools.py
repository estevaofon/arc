"""Gate test: tools in active skill's disallowed_tools list are blocked."""
from __future__ import annotations

import asyncio

from aru.agent_factory import _wrap_tools_with_hooks
from aru.config import AgentConfig, Skill
from aru.runtime import get_ctx
from aru.session import Session


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _setup_ctx(active_skill: str | None, skills: dict[str, Skill]) -> Session:
    session = Session()
    session.active_skill = active_skill
    cfg = AgentConfig()
    cfg.skills = skills
    ctx = get_ctx()
    ctx.config = cfg
    ctx.session = session
    return session


def enter_plan_mode():
    """Fake tool used as the block target — name matches a real Aru tool."""
    return "plan mode entered"


def test_disallowed_tool_blocked_when_skill_active():
    skill = Skill(
        name="writing-plans",
        description="test",
        content="",
        source_path="/fake",
        disallowed_tools=["enter_plan_mode"],
    )
    _setup_ctx("writing-plans", {"writing-plans": skill})

    wrapped = _wrap_tools_with_hooks([enter_plan_mode])[0]
    result = _run(wrapped())

    assert "BLOCKED" in result
    assert "writing-plans" in result
    assert "enter_plan_mode" in result


def test_tool_allowed_when_not_in_disallowed_list():
    skill = Skill(
        name="writing-plans",
        description="",
        content="",
        source_path="/fake",
        disallowed_tools=["bash"],
    )
    _setup_ctx("writing-plans", {"writing-plans": skill})

    wrapped = _wrap_tools_with_hooks([enter_plan_mode])[0]
    result = _run(wrapped())

    assert result == "plan mode entered"


def test_tool_allowed_when_no_active_skill():
    _setup_ctx(None, {})
    wrapped = _wrap_tools_with_hooks([enter_plan_mode])[0]
    result = _run(wrapped())
    assert result == "plan mode entered"


def test_tool_allowed_when_active_skill_missing_from_config():
    """Stale session.active_skill name (skill removed from config) is a no-op."""
    _setup_ctx("deleted-skill", {})
    wrapped = _wrap_tools_with_hooks([enter_plan_mode])[0]
    result = _run(wrapped())
    assert result == "plan mode entered"


def exit_plan_mode(plan: str = ""):
    """Fake exit_plan_mode tool — name MUST match the real tool for the exemption."""
    return "plan exited"


def test_exit_plan_mode_is_always_allowed_even_if_disallowed_by_skill():
    """A skill that lists exit_plan_mode in disallowed_tools must NOT trap the
    agent. The gate hard-exempts exit_plan_mode so the agent always has a way
    out of plan mode, regardless of skill configuration."""
    skill = Skill(
        name="writing-plans",
        description="",
        content="",
        source_path="/fake",
        disallowed_tools=["exit_plan_mode", "bash"],
    )
    _setup_ctx("writing-plans", {"writing-plans": skill})

    wrapped = _wrap_tools_with_hooks([exit_plan_mode])[0]
    result = _run(wrapped(plan="test plan"))

    assert result == "plan exited"
    assert "BLOCKED" not in result


def test_subagent_does_not_inherit_parent_active_skill():
    """Scenario 3 fix: a subagent running with its own agent_id must not be
    subject to disallowed_tools from a skill the PARENT had active. Skills
    are keyed per agent scope via session.active_skills."""
    skill = Skill(
        name="writing-plans",
        description="",
        content="",
        source_path="/fake",
        disallowed_tools=["enter_plan_mode"],
    )
    _setup_ctx("writing-plans", {"writing-plans": skill})

    # Flip the runtime into a subagent scope — emulates fork_ctx() having
    # assigned a fresh agent_id. With C3 the gate must look up the active
    # skill for THIS scope (None) and find nothing.
    ctx = get_ctx()
    ctx.agent_id = "subagent-test"

    wrapped = _wrap_tools_with_hooks([enter_plan_mode])[0]
    result = _run(wrapped())

    # No BLOCKED because the subagent scope has no active skill.
    assert result == "plan mode entered"


def test_primary_gate_still_fires_when_subagent_scope_is_unrelated():
    """Dual of the previous test: switching ctx.agent_id away from None must
    not accidentally DISABLE the gate for the primary scope. Re-pinning to
    None (primary) must re-enable the gate with the same skill config."""
    skill = Skill(
        name="writing-plans",
        description="",
        content="",
        source_path="/fake",
        disallowed_tools=["enter_plan_mode"],
    )
    _setup_ctx("writing-plans", {"writing-plans": skill})

    ctx = get_ctx()
    ctx.agent_id = None  # explicit primary

    wrapped = _wrap_tools_with_hooks([enter_plan_mode])[0]
    result = _run(wrapped())
    assert "BLOCKED" in result
