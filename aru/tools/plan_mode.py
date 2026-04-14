"""Plan mode control surface — agent-invokable tool to generate a structured plan.

This is the autonomous counterpart to the `/plan` slash command. The build
agent calls `enter_plan_mode(task)` when it detects a request requiring
multiple coordinated changes; the tool runs the planner via runner.prompt,
stores the plan in the session, and returns a summary so the build agent can
immediately follow the resulting PLAN ACTIVE reminder.
"""

from __future__ import annotations

from aru.runtime import get_ctx


async def enter_plan_mode(task: str) -> str:
    """Generate a structured plan for a complex task before executing.

    Use this when the user asks for work that requires 3+ coordinated changes
    across files. Generates a read-only plan via the planner agent, stores it
    in the session, and returns the plan text. After this returns, a PLAN
    ACTIVE system reminder will appear in your context — follow it: execute
    each step in order and call update_plan_step(index, "completed") as you go.

    Do NOT call this if a plan is already active — execute the existing plan.

    Args:
        task: One-line description of what to plan.
    """
    ctx = get_ctx()
    session = ctx.session
    if session is None:
        return "Error: enter_plan_mode requires an active session."

    if getattr(session, "plan_steps", None):
        return (
            "Error: a plan is already active. Execute the existing plan steps "
            "(see the PLAN ACTIVE reminder) instead of replanning."
        )

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
    return (
        f"Plan stored: {n_steps} steps. The PLAN ACTIVE reminder will appear in "
        f"your next context window — execute steps in order and call "
        f"update_plan_step(index, 'completed') after each.\n\n"
        f"--- PLAN ---\n{plan_content}"
    )
