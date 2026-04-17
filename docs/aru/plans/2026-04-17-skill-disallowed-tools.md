# Skill `disallowed-tools` Enforcement — Implementation Plan

> **For agentic workers:** Use `/executing-plans` (sequential) to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow a skill's SKILL.md frontmatter to declare `disallowed-tools: ...` so that tool calls to those names are hard-blocked while the skill is active — solving the brainstorming/writing-plans case where the model drifts into `enter_plan_mode` instead of writing the plan to a `.md` file.

**Architecture:** Three surgical changes. (1) Parse a new `disallowed-tools` frontmatter field into the `Skill` dataclass. (2) Track the *currently active skill* as a single slot on `Session`, set by both `invoke_skill` and the CLI slash-command dispatcher. (3) Gate the existing `_wrap_tools_with_hooks` wrapper in `agent_factory` so calls to disallowed tool names short-circuit with a structured error that steers the model toward the right path.

**Tech Stack:** Python 3.13, pytest, Agno agents, existing Aru permission/plan-mode gating patterns.

**Semantic revision from the design discussion:** single-slot `active_skill`, not a stack. Aru's `invoke_skill` already resets `task_store` on entry, treating skills as *replacement* rather than nested — so a stack adds complexity without modeling the real lifecycle. If nested skills become a real requirement later, upgrade this to a stack then.

**Scope boundaries (v1 does NOT include):**
- `allowed-tools` as auto-approve permission allowlist (Claude Code parity — defer to v2)
- Subagent `tools` filter (aru/tools/delegate.py — defer to v2)
- Per-skill custom error messages (generic message only)
- Explicit `exit_skill` tool

---

## File Structure

| File | Role |
|---|---|
| `aru/config.py` | Add `disallowed_tools: list[str]` to `Skill` dataclass; parse frontmatter |
| `aru/session.py` | Add `active_skill: str \| None` single-slot slot |
| `aru/tools/skill.py` | Set `session.active_skill` inside `invoke_skill` |
| `aru/cli.py` | Set `session.active_skill` in the `/skill-name` slash-command dispatch branch |
| `aru/agent_factory.py` | Gate `_wrap_tools_with_hooks` with a disallowed-tools check (mirrors the existing plan-mode gate pattern) |
| `tests/test_config.py` | Parse tests for `disallowed-tools` (mirror `allowed-tools` tests at lines 563–573) |
| `tests/test_skill_disallowed_tools.py` | NEW — behavioural test for the gate |
| `tests/test_invoke_skill.py` | Assert `session.active_skill` is set after invocation |
| `../aru-superpowers/skills/writing-plans/SKILL.md` | Declare `disallowed-tools: enter_plan_mode` + anti-pattern text |
| `../aru-superpowers/skills/brainstorming/SKILL.md` | Declare `disallowed-tools: enter_plan_mode` + hoist HARD-GATE to top |

---

## Task 1: Parse `disallowed-tools` frontmatter

**Files:**
- Modify: `aru/config.py:35-44` (Skill dataclass)
- Modify: `aru/config.py:244-269` (`_parse_skill_metadata`)
- Test: `tests/test_config.py` (append at end of the `_parse_skill_metadata` test class around line 573)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py` inside the `_parse_skill_metadata` test class:

```python
def test_disallowed_tools_comma_separated(self):
    result = _parse_skill_metadata({"disallowed-tools": "enter_plan_mode, bash"})
    assert result["disallowed_tools"] == ["enter_plan_mode", "bash"]

def test_disallowed_tools_list(self):
    result = _parse_skill_metadata({"disallowed-tools": ["enter_plan_mode", "bash"]})
    assert result["disallowed_tools"] == ["enter_plan_mode", "bash"]

def test_disallowed_tools_empty(self):
    result = _parse_skill_metadata({"disallowed-tools": ""})
    assert result["disallowed_tools"] == []

def test_disallowed_tools_missing(self):
    result = _parse_skill_metadata({})
    assert result["disallowed_tools"] == []
```

- [ ] **Step 2: Run and confirm failure**

```bash
pytest tests/test_config.py -k disallowed_tools -q
```
Expected: FAIL — `KeyError: 'disallowed_tools'` because the parser doesn't produce it yet.

- [ ] **Step 3: Add the field to the Skill dataclass**

In `aru/config.py` around line 41, after `allowed_tools`:

```python
@dataclass
class Skill:
    """A skill following the agentskills.io standard (<name>/SKILL.md)."""
    name: str
    description: str
    content: str
    source_path: str
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    disable_model_invocation: bool = False
    user_invocable: bool = True
    argument_hint: str = ""
```

- [ ] **Step 4: Extend the metadata parser**

In `aru/config.py`, inside `_parse_skill_metadata` (after the `allowed_tools` block around line 267), add:

```python
disallowed_raw = metadata.get("disallowed-tools", "")
if isinstance(disallowed_raw, list):
    result["disallowed_tools"] = [str(t).strip() for t in disallowed_raw]
elif disallowed_raw:
    result["disallowed_tools"] = [t.strip() for t in str(disallowed_raw).split(",") if t.strip()]
else:
    result["disallowed_tools"] = []
```

- [ ] **Step 5: Confirm tests pass + no regressions**

```bash
pytest tests/test_config.py -q
```
Expected: PASS (new tests + all existing skill parser tests).

- [ ] **Step 6: Commit**

```bash
git add aru/config.py tests/test_config.py
git commit -m "feat(skills): parse disallowed-tools frontmatter field"
```

---

## Task 2: Add `active_skill` slot to Session

**Files:**
- Modify: `aru/session.py:167-217` (`Session.__init__`)
- Test: `tests/test_checkpoints.py` or new — the simplest check is a single assertion; add to `tests/test_invoke_skill.py` in Task 3. No dedicated test file here.

- [ ] **Step 1: Add the attribute**

In `aru/session.py`, inside `Session.__init__` after the `self.plan_mode` block (around line 179):

```python
# Currently active skill name, if any. Set by invoke_skill and by the
# CLI slash-command dispatcher when the user invokes /<skill>. Consulted
# by the tool wrapper in agent_factory to enforce `disallowed_tools`.
# Single slot: invoking a new skill replaces the previous one, matching
# the existing task_store.reset() replacement semantic in invoke_skill.
self.active_skill: str | None = None
```

- [ ] **Step 2: Verify no regressions**

```bash
pytest tests/test_checkpoints.py tests/test_cli_session.py -q
```
Expected: PASS (no existing test references `active_skill`).

- [ ] **Step 3: Commit**

```bash
git add aru/session.py
git commit -m "feat(session): add active_skill slot for skill-aware tool gating"
```

---

## Task 3: Set `active_skill` in `invoke_skill`

**Files:**
- Modify: `aru/tools/skill.py:53-120` (`invoke_skill`)
- Test: `tests/test_invoke_skill.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_invoke_skill.py`:

```python
def test_invoke_skill_sets_active_skill(monkeypatch):
    """After a successful invoke_skill call, session.active_skill == skill name."""
    from aru.tools.skill import invoke_skill
    from aru.config import Skill
    from aru.session import Session
    from aru.runtime import RuntimeContext, set_ctx

    session = Session()
    assert session.active_skill is None

    skill = Skill(
        name="writing-plans",
        description="test",
        content="Hello from writing-plans",
        source_path="/fake/path",
        disallowed_tools=["enter_plan_mode"],
    )

    class FakeConfig:
        skills = {"writing-plans": skill}

    ctx = RuntimeContext()
    ctx.config = FakeConfig()
    ctx.session = session
    set_ctx(ctx)

    result = invoke_skill("writing-plans")
    assert "SKILL_CONTENT" in result
    assert session.active_skill == "writing-plans"
```

- [ ] **Step 2: Run and confirm failure**

```bash
pytest tests/test_invoke_skill.py::test_invoke_skill_sets_active_skill -q
```
Expected: FAIL — `session.active_skill` stays `None`.

- [ ] **Step 3: Implement**

In `aru/tools/skill.py`, inside `invoke_skill`, after the `ctx.task_store.reset()` try/except (line ~99) and before `render_skill_template` is called:

```python
    # Mark this skill as active so the tool wrapper can enforce the
    # skill's disallowed_tools list. Single-slot: replaces any previously
    # active skill. Cleared when another skill is invoked or at session end.
    session = getattr(ctx, "session", None)
    if session is not None:
        session.active_skill = cleaned
```

- [ ] **Step 4: Confirm tests pass**

```bash
pytest tests/test_invoke_skill.py -q
```
Expected: PASS — new test plus all existing tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add aru/tools/skill.py tests/test_invoke_skill.py
git commit -m "feat(skills): set session.active_skill on invoke_skill"
```

---

## Task 4: Set `active_skill` in the CLI slash-command dispatcher

**Files:**
- Modify: `aru/cli.py:665-675` (skill branch of the `/cmd` dispatch)

No new test — Task 5 (end-to-end) will cover this path.

- [ ] **Step 1: Modify the CLI skill branch**

In `aru/cli.py` around line 669, inside the `elif cmd_name in config.skills:` branch, before `render_skill_template`:

```python
            elif cmd_name in config.skills:
                skill = config.skills[cmd_name]
                if not skill.user_invocable:
                    console.print(f"[yellow]Skill '{cmd_name}' is not user-invocable[/yellow]")
                else:
                    session.active_skill = cmd_name
                    prompt = render_skill_template(skill.content, cmd_args)
                    console.print(f"[bold magenta]Running skill /{cmd_name}...[/bold magenta]")

                    agent = await create_general_agent(session, config, env_context=_build_env_ctx())
                    session.add_message("user", user_input)
                    await run_agent_capture(agent, prompt, session, images=attached_images or None)
```

- [ ] **Step 2: Smoke-check with existing CLI tests**

```bash
pytest tests/test_cli.py tests/test_cli_advanced.py -q
```
Expected: PASS (the change is additive — an attribute assignment).

- [ ] **Step 3: Commit**

```bash
git add aru/cli.py
git commit -m "feat(cli): set session.active_skill when dispatching /skill-name"
```

---

## Task 5: Enforce `disallowed-tools` in `_wrap_tools_with_hooks`

This is the heart of the feature. Mirrors the existing plan-mode gate pattern at `aru/agent_factory.py:69-83`.

**Files:**
- Modify: `aru/agent_factory.py:60-111` (`_wrap_tools_with_hooks`)
- Create: `tests/test_skill_disallowed_tools.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_skill_disallowed_tools.py`:

```python
"""Gate test: tools in active skill's disallowed_tools list are blocked."""
from __future__ import annotations

import asyncio

import pytest

from aru.agent_factory import _wrap_tools_with_hooks
from aru.config import Skill
from aru.runtime import RuntimeContext, set_ctx
from aru.session import Session


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _setup_ctx(active_skill: str | None, skills: dict[str, Skill]) -> Session:
    session = Session()
    session.active_skill = active_skill

    class FakeConfig:
        pass

    cfg = FakeConfig()
    cfg.skills = skills

    ctx = RuntimeContext()
    ctx.config = cfg
    ctx.session = session
    set_ctx(ctx)
    return session


def _fake_tool():
    def enter_plan_mode():
        return "plan mode entered"
    return enter_plan_mode


def test_disallowed_tool_blocked_when_skill_active():
    skill = Skill(
        name="writing-plans",
        description="test",
        content="",
        source_path="/fake",
        disallowed_tools=["enter_plan_mode"],
    )
    _setup_ctx("writing-plans", {"writing-plans": skill})

    wrapped = _wrap_tools_with_hooks([_fake_tool()])[0]
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

    wrapped = _wrap_tools_with_hooks([_fake_tool()])[0]
    result = _run(wrapped())

    assert result == "plan mode entered"


def test_tool_allowed_when_no_active_skill():
    _setup_ctx(None, {})
    wrapped = _wrap_tools_with_hooks([_fake_tool()])[0]
    result = _run(wrapped())
    assert result == "plan mode entered"


def test_tool_allowed_when_active_skill_missing_from_config():
    """Stale session.active_skill name (skill removed from config) is a no-op."""
    _setup_ctx("deleted-skill", {})
    wrapped = _wrap_tools_with_hooks([_fake_tool()])[0]
    result = _run(wrapped())
    assert result == "plan mode entered"
```

- [ ] **Step 2: Run and confirm failure**

```bash
pytest tests/test_skill_disallowed_tools.py -q
```
Expected: FAIL — `test_disallowed_tool_blocked_when_skill_active` returns the tool's success string instead of `BLOCKED`.

- [ ] **Step 3: Implement the gate**

In `aru/agent_factory.py`, inside `_wrap_one.wrapper`, AFTER the plan-mode gate block (after line 83) and BEFORE the plugin `before` hook (before line 85), insert:

```python
            # Active-skill disallowed-tools gate — fires after the plan-mode
            # gate and before plugin hooks so skill-declared blocks are honored
            # regardless of permission/plugin state. Mirrors the plan-mode gate
            # pattern above.
            try:
                from aru.runtime import get_ctx
                ctx = get_ctx()
                session = getattr(ctx, "session", None)
                config = getattr(ctx, "config", None)
            except (LookupError, AttributeError):
                session = None
                config = None
            if session is not None and config is not None:
                active = getattr(session, "active_skill", None)
                skills = getattr(config, "skills", None) or {}
                active_skill_obj = skills.get(active) if active else None
                disallowed = getattr(active_skill_obj, "disallowed_tools", None) or []
                if tool_name in disallowed:
                    return (
                        f"BLOCKED: tool `{tool_name}` is disallowed by the "
                        f"currently active skill `{active}`. Read the skill's "
                        f"SKILL.md for the prescribed path. Do NOT retry "
                        f"`{tool_name}`; use the alternative the skill specifies "
                        f"(commonly: write the output to a `.md` file via "
                        f"`write_file` instead of using in-session state)."
                    )
```

- [ ] **Step 4: Run and confirm pass**

```bash
pytest tests/test_skill_disallowed_tools.py -q
```
Expected: PASS (all four cases).

- [ ] **Step 5: Run the full suite to check for regressions**

```bash
pytest -q
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add aru/agent_factory.py tests/test_skill_disallowed_tools.py
git commit -m "feat(skills): enforce disallowed-tools gate in tool wrapper"
```

---

## Task 6: Update aru-superpowers skills

**Files:**
- Modify: `../aru-superpowers/skills/writing-plans/SKILL.md`
- Modify: `../aru-superpowers/skills/brainstorming/SKILL.md`

These live in a sibling repo (`D:\OneDrive\Documentos\python_projects\aru-superpowers`). No code tests — the gate tests in Task 5 already prove enforcement. The SKILL.md edits are what actually wire the feature up for the user's real workflow.

- [ ] **Step 1: Edit `writing-plans` frontmatter**

In `aru-superpowers/skills/writing-plans/SKILL.md`, change the frontmatter block at the top to:

```yaml
---
name: writing-plans
description: Use when you have a spec or requirements for a multi-step task, before touching code. Produces a bite-sized checkbox plan a junior engineer could execute.
argument-hint: "[feature-description-or-path-to-spec]"
user-invocable: true
allowed-tools: read_file, read_files, write_file, grep_search, glob_search, list_directory, create_task_list, update_task
disallowed-tools: enter_plan_mode
---
```

- [ ] **Step 2: Add anti-pattern text in `writing-plans`**

In the same file, in the `## Anti-Patterns to Avoid` section (around line 104), prepend a new bullet:

```markdown
- **Never call `enter_plan_mode`**: that tool stores plans in volatile session state and is blocked by this skill. The plan is a `.md` file written by `write_file` to `docs/aru/plans/YYYY-MM-DD-<feature>.md`. If you catch yourself wanting to call `enter_plan_mode`, you have misread the skill — go back to the "Final Output" section.
```

- [ ] **Step 3: Edit `brainstorming` frontmatter**

In `aru-superpowers/skills/brainstorming/SKILL.md`, add `disallowed-tools` to the frontmatter:

```yaml
---
name: brainstorming
description: Use BEFORE any creative work — creating features, building components, adding functionality, or modifying behavior. Explores user intent, requirements, and design before implementation.
argument-hint: "[idea-or-feature-topic]"
user-invocable: true
allowed-tools: read_file, read_files, glob_search, grep_search, list_directory, bash, write_file, create_task_list, update_task
disallowed-tools: enter_plan_mode, edit_file, write_file
---
```

Rationale: brainstorming also writes files (the spec doc at the end), so `write_file` needs to NOT be in disallowed... reconsider — remove `write_file` from the disallowed list. Revised:

```yaml
disallowed-tools: enter_plan_mode
```

(Leave the rest of the frontmatter as-is.)

- [ ] **Step 4: Hoist the HARD-GATE in `brainstorming`**

In `aru-superpowers/skills/brainstorming/SKILL.md`, move the `<HARD-GATE>` block (lines 15–19 in the current file) to be the FIRST content after the frontmatter, before the `# Brainstorming Ideas Into Designs` heading. Rewrite it to reference the new enforcement:

```markdown
<HARD-GATE>
Do NOT invoke any implementation skill, write code, scaffold any project, or take implementation action until a design is presented and the user has approved it. This applies to EVERY project regardless of perceived simplicity.

`enter_plan_mode` is blocked by this skill at the tool level — it will return a `BLOCKED` error. The correct path is: brainstorm → write spec to `docs/aru/specs/` via `write_file` → `invoke_skill("writing-plans")`.
</HARD-GATE>
```

- [ ] **Step 5: Manual verification**

Start Aru in a scratch project, invoke `/brainstorming`, and attempt `enter_plan_mode` via model instruction. The gate must emit `BLOCKED: tool enter_plan_mode is disallowed by the currently active skill brainstorming ...`. Record the transcript snippet in the PR description.

- [ ] **Step 6: Commit the aru-superpowers changes**

```bash
cd ../aru-superpowers
git add skills/writing-plans/SKILL.md skills/brainstorming/SKILL.md
git commit -m "feat: declare disallowed-tools=enter_plan_mode on writing-plans and brainstorming"
```

---

## Self-Review Checklist

After writing this plan, walk it end-to-end:

- [ ] Every code block has a file path and a surrounding context reference (function name or line number).
- [ ] Every TDD pair (test → implementation) has a run command with expected outcome.
- [ ] No `TODO`, `...`, or "similar to Task N" placeholders anywhere in code blocks.
- [ ] Tests cover: the four branch cases (blocked / not in list / no active skill / stale skill name) AND the parser paths (comma / list / empty / missing).
- [ ] The CLI dispatcher change (Task 4) is smoke-covered by existing CLI tests — no new test required.
- [ ] aru-superpowers edits reference frontmatter exactly as declared so the parser in Task 1 accepts them.

If any item fails, fix the plan before executing.

---

## Out of Scope — Tracked for v2

- Port `allowed-tools` as auto-approve permission allowlist (Claude Code `alwaysAllowRules.command` parity). Extends `aru/permissions.py::resolve_permission` to honor the active skill's `allowed_tools` as an auto-approve list in addition to existing session/cli/config rules.
- Subagent tool filter: in `aru/tools/delegate.py`, filter `_SUBAGENT_TOOLS` by `agent_def.tools` before registering in Agno, mirroring Claude Code's `resolveAgentTools` (`agentToolUtils.ts:122-225`). This blocks tools at the LLM schema level for subagents, which is stricter than runtime rejection.
- Optional per-skill `disallowed-tools-message` frontmatter field if the generic error message proves insufficient in practice.
- Explicit `exit_skill` tool to clear `session.active_skill` without invoking another skill.
