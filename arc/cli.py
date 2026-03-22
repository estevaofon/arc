"""Interactive CLI for arc - a Claude Code clone."""

import os
import subprocess
import sys

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.text import Text

from arc.agents.executor import create_executor
from arc.agents.planner import create_planner

console = Console()


WELCOME = """\
# arc

A coding agent powered by Claude + Agno.

**Commands:**
- `/plan <task>` — Create an implementation plan
- `/exec [task]` — Execute the current plan, or a specific task
- `! <command>` — Run a shell command directly
- `/quit` — Exit

Or just type naturally — arc will decide whether to plan or execute.
"""

GENERAL_INSTRUCTIONS = """\
You are arc, an AI coding assistant. You help users with software engineering tasks.

You have access to tools for reading, writing, and editing files, searching the codebase, and running shell commands.

Be concise and direct. Focus on doing the work, not explaining what you'll do.
The current working directory is: {cwd}

{context}
"""


class Session:
    """Holds shared state across the conversation."""

    def __init__(self):
        self.history: list[dict[str, str]] = []
        self.current_plan: str | None = None
        self.plan_task: str | None = None

    def add_message(self, role: str, content: str):
        self.history.append({"role": role, "content": content})
        if len(self.history) > 40:
            self.history = self.history[-40:]

    def get_context_summary(self) -> str:
        """Build context string from conversation history and active plan."""
        parts = []
        if self.current_plan:
            parts.append(f"## Active Plan\nTask: {self.plan_task}\n\n{self.current_plan}")
        if self.history:
            parts.append("## Conversation History")
            for msg in self.history[-10:]:
                prefix = "User" if msg["role"] == "user" else "Assistant"
                content = msg["content"]
                if len(content) > 500:
                    content = content[:500] + "..."
                parts.append(f"**{prefix}:** {content}")
        return "\n\n".join(parts)


def create_general_agent(session: Session):
    """Create the general-purpose agent."""
    from agno.agent import Agent
    from agno.models.anthropic import Claude

    from arc.tools.codebase import ALL_TOOLS

    return Agent(
        name="Arc",
        model=Claude(id="claude-sonnet-4-5-20250929"),
        tools=ALL_TOOLS,
        instructions=GENERAL_INSTRUCTIONS.format(
            cwd=os.getcwd(),
            context=session.get_context_summary(),
        ),
        markdown=True,
    )


def run_shell(command: str):
    """Run a shell command directly, streaming output to the terminal."""
    console.print()
    console.print(Panel(
        Syntax(command, "bash", theme="monokai"),
        title="[bold]Shell[/bold]",
        border_style="dim",
        expand=False,
    ))
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=os.getcwd(),
            bufsize=1,
        )
        for line in process.stdout:
            console.print(Text(line.rstrip()))
        process.wait()
        if process.returncode != 0:
            console.print(f"[red]Exit code: {process.returncode}[/red]")
    except KeyboardInterrupt:
        process.kill()
        console.print("\n[yellow]Interrupted.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
    console.print()


def run_agent_capture(agent, message: str) -> str | None:
    """Run agent with streaming display and capture the final content."""
    from agno.run.agent import (
        RunCompletedEvent,
        RunContentEvent,
        ToolCallCompletedEvent,
        ToolCallStartedEvent,
    )

    console.print()
    content_parts = []
    final_content = None

    try:
        with Live(Spinner("dots", text="Thinking..."), console=console, refresh_per_second=10) as live:
            accumulated = ""
            for event in agent.run(message, stream=True):
                if isinstance(event, ToolCallStartedEvent):
                    tool_name = event.tool_name if hasattr(event, "tool_name") else "tool"
                    tool_args = ""
                    if hasattr(event, "tool_args") and event.tool_args:
                        tool_args = ", ".join(
                            f"{k}={repr(v)[:60]}" for k, v in event.tool_args.items()
                        )
                    live.update(Spinner("dots", text=f"Calling {tool_name}({tool_args})..."))
                elif isinstance(event, ToolCallCompletedEvent):
                    tool_name = event.tool_name if hasattr(event, "tool_name") else "tool"
                    live.update(Spinner("dots", text=f"{tool_name} done. Thinking..."))
                elif isinstance(event, RunContentEvent):
                    if hasattr(event, "content") and event.content:
                        accumulated += event.content
                        live.update(Markdown(accumulated))
                elif isinstance(event, RunCompletedEvent):
                    if hasattr(event, "content") and event.content:
                        final_content = event.content
                        live.update(Markdown(final_content))

        if not final_content and accumulated:
            final_content = accumulated

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")

    console.print()
    return final_content


def ask_yes_no(prompt: str) -> bool:
    """Ask the user a yes/no question."""
    try:
        answer = console.input(f"[bold yellow]{prompt} (y/n):[/bold yellow] ").strip().lower()
        return answer in ("y", "yes", "s", "sim")
    except (EOFError, KeyboardInterrupt):
        return False


def run_cli():
    """Main REPL loop."""
    console.print(Markdown(WELCOME))
    console.print(Panel(
        Text(f"Working directory: {os.getcwd()}", style="dim"),
        border_style="blue",
    ))

    session = Session()
    planner = None
    executor = None

    while True:
        try:
            user_input = console.input("[bold cyan]arc>[/bold cyan] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye![/dim]")
            break

        if not user_input:
            continue

        if user_input.lower() in ("/quit", "/exit", "quit", "exit"):
            console.print("[dim]Bye![/dim]")
            break

        if user_input.startswith("! "):
            cmd = user_input[2:].strip()
            if not cmd:
                console.print("[yellow]Usage: ! <command>[/yellow]")
                continue
            run_shell(cmd)

        elif user_input.startswith("/plan "):
            task = user_input[6:].strip()
            if not task:
                console.print("[yellow]Usage: /plan <task description>[/yellow]")
                continue

            console.print("[bold magenta]Planning...[/bold magenta]")
            if planner is None:
                planner = create_planner()

            context = session.get_context_summary()
            prompt = task
            if context:
                prompt = f"{task}\n\n---\nContext from this session:\n{context}"

            plan_content = run_agent_capture(planner, prompt)

            if plan_content:
                session.current_plan = plan_content
                session.plan_task = task
                session.add_message("user", f"/plan {task}")
                session.add_message("assistant", f"[Plan]\n{plan_content}")

                if ask_yes_no("Execute this plan?"):
                    console.print("[bold green]Executing plan...[/bold green]")
                    if executor is None:
                        executor = create_executor()
                    exec_prompt = (
                        f"Execute the following plan step by step.\n\n"
                        f"## Task\n{task}\n\n"
                        f"## Plan\n{plan_content}"
                    )
                    result = run_agent_capture(executor, exec_prompt)
                    if result:
                        session.add_message("assistant", f"[Execution]\n{result}")

        elif user_input.startswith("/exec"):
            task = user_input[5:].strip()

            if not task and session.current_plan:
                console.print(f"[bold green]Executing current plan:[/bold green] [dim]{session.plan_task}[/dim]")
                if executor is None:
                    executor = create_executor()
                exec_prompt = (
                    f"Execute the following plan step by step.\n\n"
                    f"## Task\n{session.plan_task}\n\n"
                    f"## Plan\n{session.current_plan}"
                )
                result = run_agent_capture(executor, exec_prompt)
                if result:
                    session.add_message("user", "/exec (current plan)")
                    session.add_message("assistant", f"[Execution]\n{result}")
            elif not task:
                console.print("[yellow]No active plan. Usage: /exec <task> or /plan first.[/yellow]")
            else:
                console.print("[bold green]Executing...[/bold green]")
                if executor is None:
                    executor = create_executor()

                context = session.get_context_summary()
                prompt = task
                if context:
                    prompt = f"{task}\n\n---\nContext from this session:\n{context}"

                result = run_agent_capture(executor, prompt)
                if result:
                    session.add_message("user", f"/exec {task}")
                    session.add_message("assistant", f"[Execution]\n{result}")

        else:
            agent = create_general_agent(session)
            session.add_message("user", user_input)
            result = run_agent_capture(agent, user_input)
            if result:
                session.add_message("assistant", result)


def main():
    """Entry point for the arc CLI."""
    from dotenv import load_dotenv

    load_dotenv()
    run_cli()
