"""Interactive CLI for arc - a Claude Code clone."""

import asyncio
import os
import random
import subprocess
import sys
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from rich.console import Console, ConsoleOptions, RenderResult
from rich.live import Live
from rich.markdown import Markdown
from rich.measure import Measurement
from rich.panel import Panel
from rich.spinner import Spinner
from rich.syntax import Syntax
from rich.text import Text

from arc.agents.executor import create_executor
from arc.agents.planner import create_planner

console = Console()

AVAILABLE_MODELS = {
    "sonnet": "claude-sonnet-4-5-20250929",
    "opus": "claude-opus-4-20250514",
    "haiku": "claude-haiku-3-5-20241022",
}
DEFAULT_MODEL = "sonnet"


def _sanitize_input(text: str) -> str:
    """Remove lone UTF-16 surrogates that Windows clipboard can introduce."""
    return text.encode("utf-8", errors="replace").decode("utf-8")


WELCOME = """\
# arc

A coding agent powered by Claude + Agno.

**Commands:**
- `/plan <task>` — Create an implementation plan
- `/exec [task]` — Execute the current plan, or a specific task
- `/model [sonnet|opus|haiku]` — Switch model (default: sonnet)
- `! <command>` — Run a shell command directly
- `/quit` — Exit

Or just type naturally — arc will decide whether to plan or execute.
Paste code freely — multi-line paste is detected automatically. Type a message about the paste, then Enter to send.
"""


class PasteState:
    """Tracks pasted content so the user can annotate it."""

    def __init__(self):
        self.pasted_content: str | None = None
        self.line_count: int = 0

    def set(self, content: str):
        lines = content.splitlines()
        self.pasted_content = content
        self.line_count = len(lines)

    def clear(self):
        self.pasted_content = None
        self.line_count = 0

    def build_message(self, user_text: str) -> str:
        """Combine user annotation with pasted content."""
        if self.pasted_content and user_text.strip():
            return f"{user_text.strip()}\n\n```\n{self.pasted_content}\n```"
        if self.pasted_content:
            return self.pasted_content
        return user_text


def _create_prompt_session(paste_state: PasteState) -> PromptSession:
    """Create a prompt_toolkit session with smart paste detection."""
    bindings = KeyBindings()

    @bindings.add(Keys.Escape, Keys.Enter)
    def _newline(event):
        """Escape+Enter inserts a newline for manual multi-line editing."""
        event.current_buffer.insert_text("\n")

    session = PromptSession(
        key_bindings=bindings,
        multiline=False,
        enable_open_in_editor=False,
    )

    @bindings.add(Keys.BracketedPaste)
    def _handle_paste(event):
        """Intercept multi-line pastes: store content and show line count."""
        data = event.data
        lines = data.splitlines()
        if len(lines) > 1:
            paste_state.set(data)
            event.current_buffer.reset()
            # Dynamically enable toolbar now that paste exists
            session.bottom_toolbar = HTML(
                f'  <b><style bg="ansiblue" fg="ansiwhite"> {paste_state.line_count} lines pasted </style></b>'
                f'  <i><style fg="ansigray">Type a message about this paste, or press Enter to send as-is</style></i>'
            )
            event.app.invalidate()
        else:
            event.current_buffer.insert_text(data)

    return session

GENERAL_INSTRUCTIONS = """\
You are arc, an AI coding assistant. You help users with software engineering tasks.

You have access to tools for reading, writing, and editing files, searching the codebase, running shell commands, fetching web content, and delegating subtasks to sub-agents.

Use delegate_task when you can split work into independent subtasks that benefit from parallel execution. \
For example, researching one part of the codebase while modifying another, or implementing changes in \
unrelated files simultaneously. You can call delegate_task multiple times in a single response to run sub-agents in parallel.

Be concise and direct. Focus on doing the work, not explaining what you'll do.
When creating or updating multiple independent files, use write_files to batch them in a single call instead of calling write_file repeatedly.
When making independent edits across files, use edit_files to batch them in a single call instead of calling edit_file repeatedly.
NEVER create documentation files (*.md) unless the user explicitly asks for them. This includes README.md, CHANGELOG.md, CONTRIBUTING.md, SETUP.md, and any other markdown files. A single README.md with basic usage is acceptable only when creating a new project from scratch — nothing more. Focus on writing working code, not documentation.
The current working directory is: {cwd}

{context}
"""


class Session:
    """Holds shared state across the conversation."""

    def __init__(self):
        self.history: list[dict[str, str]] = []
        self.current_plan: str | None = None
        self.plan_task: str | None = None
        self.model_key: str = DEFAULT_MODEL

    @property
    def model_id(self) -> str:
        return AVAILABLE_MODELS[self.model_key]

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
        model=Claude(id=session.model_id),
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


THINKING_PHRASES = [
    "Thinking...",
    "Cooking...",
    "Working...",
    "Making magic...",
    "Brewing ideas...",
    "Crunching code...",
    "Connecting the dots...",
    "Crafting a plan...",
    "On it...",
    "Diving deep...",
    "Almost there...",
    "Putting pieces together...",
    "Wiring things up...",
    "Spinning up neurons...",
    "Loading creativity...",
]


class StatusBar:
    """A bottom status bar that cycles through fun phrases.

    Renders as a thin separator line + spinner text.  Rich's Live calls
    ``__rich_console__`` on every refresh tick, so we rotate the phrase
    based on wall-clock time — no extra threads needed.
    """

    def __init__(self, interval: float = 3.0):
        self._interval = interval
        self._phrases = list(THINKING_PHRASES)
        random.shuffle(self._phrases)
        self._index = 0
        self._last_switch = time.monotonic()
        self._override: str | None = None

    @property
    def current_text(self) -> str:
        if self._override is not None:
            return self._override
        return self._phrases[self._index % len(self._phrases)]

    def set_text(self, text: str):
        self._override = text

    def resume_cycling(self):
        self._override = None
        self._last_switch = time.monotonic()

    def _maybe_rotate(self):
        now = time.monotonic()
        if now - self._last_switch >= self._interval:
            self._last_switch = now
            self._index += 1
            if self._index >= len(self._phrases):
                random.shuffle(self._phrases)
                self._index = 0
            self._override = None

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        self._maybe_rotate()
        spinner = Spinner("dots", text=f"[dim]{self.current_text}[/dim]", style="cyan")
        yield from spinner.__rich_console__(console, options)

    def __rich_measure__(self, console: Console, options: ConsoleOptions) -> Measurement:
        return Measurement(1, options.max_width)


TOOL_DISPLAY_NAMES = {
    "read_file": "Read",
    "write_file": "Write",
    "write_files": "Write",
    "edit_file": "Edit",
    "edit_files": "Edit",
    "glob_search": "Glob",
    "grep_search": "Grep",
    "list_directory": "List",
    "bash": "Bash",
}

TOOL_PRIMARY_ARG = {
    "read_file": "file_path",
    "write_file": "file_path",
    "edit_file": "file_path",
    "glob_search": "pattern",
    "grep_search": "pattern",
    "list_directory": "directory",
    "bash": "command",
}


def _format_tool_label(tool_name: str, tool_args: dict | None) -> str:
    """Format a tool call into a Claude Code-style label like Read(file_path)."""
    display = TOOL_DISPLAY_NAMES.get(tool_name, tool_name)
    if not tool_args:
        return display

    # Batch tools: show count
    if tool_name == "write_files":
        files = tool_args.get("files", [])
        return f"{display}({len(files)} files)"
    if tool_name == "edit_files":
        edits = tool_args.get("edits", [])
        return f"{display}({len(edits)} edits)"

    # Single-arg tools: show the primary arg value
    primary_key = TOOL_PRIMARY_ARG.get(tool_name)
    if primary_key and primary_key in tool_args:
        value = str(tool_args[primary_key])
        # Truncate long values
        if len(value) > 60:
            value = value[:57] + "..."
        return f"{display}({value})"

    return display


class StreamingDisplay:
    """Shows only un-flushed streaming content + status bar.

    Tool activity is printed as static output (above Live), not inside Live.
    When a permission prompt pauses Live, flush() is called to mark current
    content as already printed — so Live doesn't re-render it when it resumes.
    """

    def __init__(self, status_bar: StatusBar):
        self.status_bar = status_bar
        self._flushed_len: int = 0       # how much of accumulated was already printed
        self._accumulated: str = ""       # full accumulated content
        self._content: Markdown | None = None

    def set_content(self, accumulated: str):
        """Update with the full accumulated content; only the un-flushed part is displayed."""
        self._accumulated = accumulated
        delta = accumulated[self._flushed_len:]
        self.content = Markdown(delta) if delta else None

    def flush(self):
        """Print current un-flushed content statically and mark it as flushed."""
        delta = self._accumulated[self._flushed_len:]
        if delta:
            console.print(Markdown(delta))
        self._flushed_len = len(self._accumulated)
        self.content = None

    @property
    def content(self) -> Markdown | None:
        return self._content

    @content.setter
    def content(self, value: Markdown | None):
        self._content = value

    def __rich_console__(self, rconsole: Console, options: ConsoleOptions) -> RenderResult:
        if self._content is not None:
            yield self._content
            yield Text()
        yield self.status_bar

    def __rich_measure__(self, rconsole: Console, options: ConsoleOptions) -> Measurement:
        return Measurement(1, options.max_width)


async def run_agent_capture(agent, message: str) -> str | None:
    """Run agent with async streaming display and parallel tool execution."""
    from agno.run.agent import (
        RunCompletedEvent,
        RunContentEvent,
        ToolCallCompletedEvent,
        ToolCallStartedEvent,
    )

    console.print()
    final_content = None

    try:
        from arc.tools.codebase import set_display, set_live

        status = StatusBar(interval=3.0)
        display = StreamingDisplay(status)
        current_tool_label: str | None = None

        with Live(display, console=console, refresh_per_second=10) as live:
            set_live(live)
            set_display(display)
            accumulated = ""
            async for event in agent.arun(message, stream=True):
                if isinstance(event, ToolCallStartedEvent):
                    tool_name = event.tool_name if hasattr(event, "tool_name") else "tool"
                    tool_args = event.tool_args if hasattr(event, "tool_args") else None
                    current_tool_label = _format_tool_label(tool_name, tool_args)
                    # Flush any accumulated content before tool runs
                    if accumulated[display._flushed_len:]:
                        live.stop()
                        display.flush()
                        live.start()
                    status.set_text(f"{current_tool_label}...")
                    live.update(display)

                elif isinstance(event, ToolCallCompletedEvent):
                    if current_tool_label:
                        # Print completed tool as static output above Live
                        live.console.print(Text.assemble(
                            ("  ", ""),
                            ("\u2713 ", "bold green"),
                            (current_tool_label, "dim"),
                        ))
                        current_tool_label = None
                    status.resume_cycling()
                    live.update(display)

                elif isinstance(event, RunContentEvent):
                    if hasattr(event, "content") and event.content:
                        accumulated += event.content
                        display.set_content(accumulated)
                        live.update(display)

                elif isinstance(event, RunCompletedEvent):
                    if hasattr(event, "content") and event.content:
                        final_content = event.content

        set_live(None)
        set_display(None)

        # Print only un-flushed content
        if final_content:
            # RunCompletedEvent returns full content — only print the un-flushed tail
            if display._flushed_len > 0:
                remaining = final_content[display._flushed_len:]
                if remaining:
                    console.print(Markdown(remaining))
            else:
                console.print(Markdown(final_content))
        elif accumulated[display._flushed_len:]:
            final_content = accumulated
            console.print(Markdown(accumulated[display._flushed_len:]))

    except KeyboardInterrupt:
        set_live(None)
        set_display(None)
        console.print("\n[yellow]Interrupted.[/yellow]")
    except Exception as e:
        set_live(None)
        set_display(None)
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


async def run_cli(skip_permissions: bool = False):
    """Main REPL loop."""
    from arc.tools.codebase import set_console, set_model_id, set_skip_permissions, reset_allowed_actions
    set_console(console)
    set_skip_permissions(skip_permissions)

    console.print(Markdown(WELCOME))
    console.print(Panel(
        Text(f"Working directory: {os.getcwd()}", style="dim"),
        border_style="blue",
    ))
    mode = "[bold red]skip permissions[/bold red]" if skip_permissions else "[bold green]safe mode[/bold green]"
    console.print(f"[dim]Model: [bold]{DEFAULT_MODEL}[/bold] ({AVAILABLE_MODELS[DEFAULT_MODEL]}) | {mode}[/dim]\n")

    session = Session()
    planner = None
    executor = None
    paste_state = PasteState()
    prompt_session = _create_prompt_session(paste_state)

    while True:
        try:
            paste_state.clear()
            prompt_session.bottom_toolbar = None
            user_text = (
                await asyncio.to_thread(
                    prompt_session.prompt,
                    HTML(f"<b><cyan>arc</cyan></b> <style fg='ansigray'>({session.model_key})</style><b><cyan>&gt;</cyan></b> "),
                    multiline=False,
                )
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye![/dim]")
            break

        user_input = _sanitize_input(paste_state.build_message(user_text))

        if paste_state.pasted_content and user_text:
            console.print(
                f"[dim] {paste_state.line_count} lines pasted[/dim]  [cyan]{user_text}[/cyan]"
            )
        elif paste_state.pasted_content:
            console.print(
                f"[dim] {paste_state.line_count} lines pasted[/dim]"
            )

        if not user_input:
            continue

        # Reset "allow all" approvals for each new user message
        reset_allowed_actions()

        if user_input.lower() in ("/quit", "/exit", "quit", "exit"):
            console.print("[dim]Bye![/dim]")
            break

        if user_input.startswith("/model"):
            arg = user_input[6:].strip().lower()
            if not arg:
                console.print(f"[bold]Current model:[/bold] {session.model_key} ({session.model_id})")
                console.print(f"[dim]Available: {', '.join(AVAILABLE_MODELS.keys())}[/dim]")
            elif arg in AVAILABLE_MODELS:
                session.model_key = arg
                set_model_id(session.model_id)
                planner = None
                executor = None
                console.print(f"[bold green]Switched to {arg}[/bold green] ({AVAILABLE_MODELS[arg]})")
            else:
                console.print(f"[yellow]Unknown model '{arg}'. Available: {', '.join(AVAILABLE_MODELS.keys())}[/yellow]")
            continue

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
                planner = create_planner(session.model_id)

            context = session.get_context_summary()
            prompt = task
            if context:
                prompt = f"{task}\n\n---\nContext from this session:\n{context}"

            plan_content = await run_agent_capture(planner, prompt)

            if plan_content:
                session.current_plan = plan_content
                session.plan_task = task
                session.add_message("user", f"/plan {task}")
                session.add_message("assistant", f"[Plan]\n{plan_content}")

                if ask_yes_no("Execute this plan?"):
                    console.print("[bold green]Executing plan...[/bold green]")
                    if executor is None:
                        executor = create_executor(session.model_id)
                    exec_prompt = (
                        f"Execute the following plan step by step.\n\n"
                        f"## Task\n{task}\n\n"
                        f"## Plan\n{plan_content}"
                    )
                    result = await run_agent_capture(executor, exec_prompt)
                    if result:
                        session.add_message("assistant", f"[Execution]\n{result}")

        elif user_input.startswith("/exec"):
            task = user_input[5:].strip()

            if not task and session.current_plan:
                console.print(f"[bold green]Executing current plan:[/bold green] [dim]{session.plan_task}[/dim]")
                if executor is None:
                    executor = create_executor(session.model_id)
                exec_prompt = (
                    f"Execute the following plan step by step.\n\n"
                    f"## Task\n{session.plan_task}\n\n"
                    f"## Plan\n{session.current_plan}"
                )
                result = await run_agent_capture(executor, exec_prompt)
                if result:
                    session.add_message("user", "/exec (current plan)")
                    session.add_message("assistant", f"[Execution]\n{result}")
            elif not task:
                console.print("[yellow]No active plan. Usage: /exec <task> or /plan first.[/yellow]")
            else:
                console.print("[bold green]Executing...[/bold green]")
                if executor is None:
                    executor = create_executor(session.model_id)

                context = session.get_context_summary()
                prompt = task
                if context:
                    prompt = f"{task}\n\n---\nContext from this session:\n{context}"

                result = await run_agent_capture(executor, prompt)
                if result:
                    session.add_message("user", f"/exec {task}")
                    session.add_message("assistant", f"[Execution]\n{result}")

        else:
            agent = create_general_agent(session)
            session.add_message("user", user_input)
            result = await run_agent_capture(agent, user_input)
            if result:
                session.add_message("assistant", result)


def main():
    """Entry point for the arc CLI."""
    from dotenv import load_dotenv

    load_dotenv()
    skip_permissions = "--dangerously-skip-permissions" in sys.argv
    asyncio.run(run_cli(skip_permissions=skip_permissions))
