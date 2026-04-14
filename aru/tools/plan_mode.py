"""Plan mode control surface — agent-invokable tool to generate a structured plan.

This is the autonomous counterpart to the `/plan` slash command. The build
agent calls `enter_plan_mode(task)` when it detects a request requiring
multiple coordinated changes; the tool runs the planner via runner.prompt,
stores the plan in the session, and returns a summary so the build agent can
immediately follow the resulting PLAN ACTIVE reminder.
"""

from __future__ import annotations

import sys

from rich.panel import Panel

from aru.runtime import get_ctx


def _prompt_plan_approval(plan_steps: list, n_steps: int) -> tuple[bool, str]:
    """Show the plan panel and ask the user to approve execution.

    Returns (approved, feedback). If the user types free text instead of y/n,
    it is treated as feedback that the agent should use to adjust course.
    Non-interactive sessions (no TTY) auto-approve.
    """
    # Auto-approve in non-interactive sessions — there's nobody to answer.
    if not sys.stdin.isatty():
        return True, ""

    from aru.tools.tasklist import _render_plan_steps

    ctx = get_ctx()

    if ctx.live:
        ctx.live.stop()
    if ctx.display:
        try:
            ctx.display.flush()
        except Exception:
            pass

    ctx.console.print()
    ctx.console.print(_render_plan_steps(plan_steps))
    ctx.console.print(Panel(
        f"Proposed plan with [bold]{n_steps}[/bold] steps. Approve execution?",
        title="[bold cyan]Plan approval[/bold cyan]",
        border_style="cyan",
        expand=False,
    ))

    try:
        answer = ctx.console.input(
            "[bold cyan](y)es / (n)o / type feedback to revise:[/bold cyan] "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        answer = "n"
    finally:
        if ctx.live:
            try:
                ctx.live.start()
                ctx.live._live_render._shape = None
            except Exception:
                pass

    low = answer.lower()
    if not answer or low in ("y", "yes", "s", "sim", "ok"):
        return True, ""
    if low in ("n", "no", "não", "nao"):
        return False, ""
    return False, answer


async def enter_plan_mode(task: str, force: bool = False) -> str:
    """Generate a structured plan for a complex task and get user approval.

    Use this when the user asks for work that requires 3+ coordinated changes
    across files, or when they explicitly ask for a new plan. Generates a
    read-only plan via the planner agent, shows it to the user, and asks for
    explicit approval before execution proceeds.

    IMPORTANT: the plan is NOT automatically executed. After this tool
    returns, one of three things happened:
      1. User approved — tool returns the plan and you should execute it,
         calling update_plan_step(index, "completed") as you finish each step.
      2. User rejected — tool returns a rejection message. Stop, do NOT
         execute, and ask the user what they want instead.
      3. User gave free-text feedback — tool returns the feedback. Stop,
         do NOT execute, and either replan (enter_plan_mode again with the
         revised task) or discuss with the user.

    Behavior with an existing plan:
      - If the previous plan is fully terminal (all steps done/skipped/
        failed), it is automatically replaced.
      - If the previous plan still has pending or in-progress steps, this
        tool refuses UNLESS you pass force=True. Only pass force=True when
        the user explicitly asked for a new plan. Do NOT call
        update_plan_step to "close out" stale steps before replanning.

    Args:
        task: One-line description of what to plan.
        force: Pass True to replace a plan that still has unfinished steps.
    """
    ctx = get_ctx()
    session = ctx.session
    if session is None:
        return "Error: enter_plan_mode requires an active session."

    existing_steps = getattr(session, "plan_steps", None) or []
    if existing_steps:
        unfinished = [s for s in existing_steps if s.status not in ("completed", "skipped", "failed")]
        if unfinished and not force:
            pending_list = ", ".join(f"#{s.index}" for s in unfinished)
            return (
                f"Error: a plan is already active with {len(unfinished)} unfinished "
                f"step(s) ({pending_list}). If the user explicitly asked for a new "
                f"plan, retry with force=True to discard the in-progress plan. Do "
                f"NOT call update_plan_step to close out the old steps — that only "
                f"re-renders the stale plan. Otherwise, execute the existing plan "
                f"(see the PLAN ACTIVE reminder)."
            )
        session.clear_plan()

    from aru.runner import PromptInput, prompt as runner_prompt

    result = await runner_prompt(PromptInput(
        session=session,
        message=task,
        agent_name="plan",
        lightweight=True,
    ))
    plan_content = (result.content or "").strip()
    if not plan_content:
        return "Error: planner returned no content. Aborting plan_mode."

    session.set_plan(task, plan_content)
    n_steps = len(session.plan_steps)
    if n_steps == 0:
        return (
            f"Plan generated but no steps were detected. The next turn will not "
            f"see a PLAN ACTIVE reminder — execute manually based on this plan:\n\n"
            f"{plan_content}"
        )

    approved, feedback = _prompt_plan_approval(session.plan_steps, n_steps)

    # The approval prompt already rendered the plan panel inline, so suppress
    # the runner's coalesced end-of-batch render to avoid a duplicate.
    session._plan_render_pending = False

    if not approved:
        session.clear_plan()
        if feedback:
            return (
                f"User rejected the plan and gave this feedback:\n\n  {feedback}\n\n"
                f"Do NOT execute anything. Either call enter_plan_mode again with a "
                f"revised task that incorporates the feedback, or ask the user for "
                f"clarification. The previous plan has been discarded."
            )
        return (
            "User rejected the plan. Do NOT execute anything. Ask the user what "
            "they would like to change, then optionally call enter_plan_mode "
            "again with a revised task. The previous plan has been discarded."
        )

    return (
        f"User approved the plan ({n_steps} steps). Execute the steps in order "
        f"and call update_plan_step(index, 'completed') after each.\n\n"
        f"--- PLAN ---\n{plan_content}"
    )
