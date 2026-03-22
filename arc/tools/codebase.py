"""Custom tools for codebase exploration and manipulation."""

import fnmatch
import html.parser
import os
import re
import shlex
import subprocess
import threading
import textwrap

import httpx

from rich.console import Console, Group
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

_console = Console()
_skip_permissions = False
_live = None       # Reference to the active Rich Live instance
_permission_lock = threading.Lock()  # Serialize permission prompts
_allowed_actions: set[str] = set()   # Actions auto-approved via "allow all"
_display = None    # Reference to the active StreamingDisplay


def set_skip_permissions(value: bool):
    global _skip_permissions
    _skip_permissions = value


def set_live(live):
    """Set the active Live instance so tools can pause it during permission prompts."""
    global _live
    _live = live


def set_display(display):
    """Set the active StreamingDisplay so tools can flush content before permission prompts."""
    global _display
    _display = display


def set_console(console: Console):
    """Share the main console instance to avoid conflicts with Live display."""
    global _console
    _console = console


def _format_diff(old_string: str, new_string: str) -> Group:
    """Format old/new strings as a colored diff (red background for deletions, green for additions)."""
    parts = []
    if old_string:
        for line in old_string.splitlines():
            parts.append(Text.assemble(
                ("- " + line, "on red"),
            ))
    if new_string:
        for line in new_string.splitlines():
            parts.append(Text.assemble(
                ("+ " + line, "green"),
            ))
    return Group(*parts)


def reset_allowed_actions():
    """Reset auto-approved actions (call between conversations if needed)."""
    _allowed_actions.clear()


def _ask_permission(action: str, details: str | Text | Group) -> bool:
    """Ask user permission before executing a dangerous action.

    Uses a lock to serialize prompts when multiple tools run in parallel.
    Supports 'a' (allow all) to auto-approve all future calls of the same action type.
    """
    if _skip_permissions:
        return True

    if action in _allowed_actions:
        return True

    with _permission_lock:
        # Re-check after acquiring lock (another thread may have allowed it)
        if action in _allowed_actions:
            return True

        # Pause Live and flush already-streamed content so it doesn't re-render
        if _live:
            _live.stop()
        if _display:
            _display.flush()

        _console.print()
        _console.print(Panel(
            details,
            title=f"[bold yellow]{action}[/bold yellow]",
            border_style="yellow",
            expand=False,
        ))
        try:
            answer = _console.input(
                "[bold yellow]Allow? (y)es / (a)llow all / (n)o:[/bold yellow] "
            ).strip().lower()
            if answer in ("a", "allow all", "all"):
                _allowed_actions.add(action)
                allowed = True
            else:
                allowed = answer in ("y", "yes", "s", "sim")
        except (EOFError, KeyboardInterrupt):
            allowed = False

        # Resume Live display (now clean — flushed content won't re-render)
        if _live:
            _live.start()

        return allowed


def read_file(file_path: str) -> str:
    """Read the contents of a file.

    Args:
        file_path: Path to the file to read (absolute or relative to working directory).
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        numbered = [f"{i + 1:4d} | {line}" for i, line in enumerate(lines)]
        return "".join(numbered)
    except FileNotFoundError:
        return f"Error: File not found: {file_path}"
    except Exception as e:
        return f"Error reading file: {e}"


def write_file(file_path: str, content: str) -> str:
    """Write content to a file, creating parent directories if needed.

    Args:
        file_path: Path to the file to write.
        content: The content to write to the file.
    """
    preview = content[:500] + ("..." if len(content) > 500 else "")
    header = Text(file_path, style="bold")
    diff = _format_diff("", preview)
    if not _ask_permission("Write File", Group(header, Text(), diff)):
        return f"Permission denied: write to {file_path}"
    try:
        os.makedirs(os.path.dirname(file_path) or ".", exist_ok=True)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {file_path}"
    except Exception as e:
        return f"Error writing file: {e}"


def write_files(files: list[dict]) -> str:
    """Write multiple files at once. Use this instead of multiple write_file calls when creating
    or updating several files that don't depend on each other (e.g. scaffolding a project).

    Each entry in the list must have 'path' and 'content' keys.

    Args:
        files: List of dicts with 'path' (file path) and 'content' (file content) keys.
               Example: [{"path": "src/main.py", "content": "print('hello')"}, {"path": "src/utils.py", "content": "..."}]
    """
    parts = [Text(f"Write {len(files)} files:", style="bold"), Text()]
    for e in files:
        p = e.get("path", "<missing>")
        content = e.get("content", "")
        preview = content[:300] + ("..." if len(content) > 300 else "")
        parts.append(Text(p, style="bold dim"))
        parts.append(_format_diff("", preview))
        parts.append(Text())
    if not _ask_permission("Write Files", Group(*parts)):
        return f"Permission denied: batch write of {len(files)} files"

    results = []
    errors = []
    for entry in files:
        path = entry.get("path", "")
        content = entry.get("content", "")
        if not path:
            errors.append("Error: missing 'path' in entry")
            continue
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            results.append(path)
        except Exception as e:
            errors.append(f"Error writing {path}: {e}")

    parts = []
    if results:
        parts.append(f"Successfully wrote {len(results)} files: {', '.join(results)}")
    if errors:
        parts.append("\n".join(errors))
    return "\n".join(parts) or "No files to write."


def edit_file(file_path: str, old_string: str, new_string: str) -> str:
    """Replace an exact string in a file. The old_string must appear exactly once.

    Args:
        file_path: Path to the file to edit.
        old_string: The exact text to find and replace. Must be unique in the file.
        new_string: The replacement text.
    """
    header = Text(file_path, style="bold")
    diff = _format_diff(old_string, new_string)
    if not _ask_permission("Edit File", Group(header, Text(), diff)):
        return f"Permission denied: edit {file_path}"
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        count = content.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {file_path}"
        if count > 1:
            return f"Error: old_string found {count} times in {file_path}. Must be unique."

        new_content = content.replace(old_string, new_string, 1)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        return f"Successfully edited {file_path}"
    except FileNotFoundError:
        return f"Error: File not found: {file_path}"
    except Exception as e:
        return f"Error editing file: {e}"


def edit_files(edits: list[dict]) -> str:
    """Apply multiple find-and-replace edits across files in a single call. Use this instead of
    multiple edit_file calls when making independent edits to different files (or multiple edits
    to the same file, applied in order).

    Each entry must have 'path', 'old_string', and 'new_string' keys.

    Args:
        edits: List of dicts with 'path' (file path), 'old_string' (text to find), and 'new_string' (replacement).
               Example: [{"path": "src/main.py", "old_string": "foo", "new_string": "bar"}]
    """
    parts = [Text(f"Apply {len(edits)} edits:", style="bold"), Text()]
    for e in edits:
        p = e.get("path", "<missing>")
        old = e.get("old_string", "")
        new = e.get("new_string", "")
        parts.append(Text(p, style="bold dim"))
        parts.append(_format_diff(old, new))
        parts.append(Text())
    if not _ask_permission("Edit Files", Group(*parts)):
        return f"Permission denied: batch edit of {len(edits)} files"

    results = []
    errors = []
    # Cache file contents to support multiple edits to the same file
    cache: dict[str, str] = {}

    for entry in edits:
        path = entry.get("path", "")
        old = entry.get("old_string", "")
        new = entry.get("new_string", "")
        if not path or not old:
            errors.append(f"Error: missing 'path' or 'old_string' in entry")
            continue
        try:
            if path not in cache:
                with open(path, "r", encoding="utf-8") as f:
                    cache[path] = f.read()

            content = cache[path]
            count = content.count(old)
            if count == 0:
                errors.append(f"{path}: old_string not found")
                continue
            if count > 1:
                errors.append(f"{path}: old_string found {count} times, must be unique")
                continue

            cache[path] = content.replace(old, new, 1)
            results.append(path)
        except FileNotFoundError:
            errors.append(f"{path}: file not found")
        except Exception as e:
            errors.append(f"{path}: {e}")

    # Flush all modified files
    written = set()
    for path, content in cache.items():
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            written.add(path)
        except Exception as e:
            errors.append(f"Error writing {path}: {e}")

    parts = []
    if results:
        unique = list(dict.fromkeys(results))  # preserve order, dedupe
        parts.append(f"Successfully applied {len(results)} edits across {len(unique)} files: {', '.join(unique)}")
    if errors:
        parts.append("\n".join(errors))
    return "\n".join(parts) or "No edits to apply."


def glob_search(pattern: str, directory: str = ".") -> str:
    """Find files matching a glob pattern recursively.

    Args:
        pattern: Glob pattern to match (e.g. '**/*.py', 'src/**/*.ts').
        directory: Directory to search in. Defaults to current directory.
    """
    matches = []
    for root, dirs, files in os.walk(directory):
        # Skip hidden and common ignored directories
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git", "venv", ".venv")]
        for filename in files:
            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, directory)
            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(filename, pattern):
                matches.append(rel_path)

    if not matches:
        return f"No files matched pattern: {pattern}"
    return "\n".join(sorted(matches))


def grep_search(pattern: str, directory: str = ".", file_glob: str = "") -> str:
    """Search for a regex pattern in file contents.

    Args:
        pattern: Regular expression pattern to search for.
        directory: Directory to search in. Defaults to current directory.
        file_glob: Optional glob to filter which files to search (e.g. '*.py').
    """
    import re

    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"Invalid regex pattern: {e}"

    results = []
    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git", "venv", ".venv")]
        for filename in files:
            if file_glob and not fnmatch.fnmatch(filename, file_glob):
                continue
            filepath = os.path.join(root, filename)
            rel_path = os.path.relpath(filepath, directory)
            try:
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f, 1):
                        if regex.search(line):
                            results.append(f"{rel_path}:{i}: {line.rstrip()}")
            except (OSError, PermissionError):
                continue

    if not results:
        return f"No matches found for pattern: {pattern}"
    if len(results) > 100:
        return "\n".join(results[:100]) + f"\n... and {len(results) - 100} more matches"
    return "\n".join(results)


def list_directory(directory: str = ".") -> str:
    """List files and directories in the given path.

    Args:
        directory: Directory to list. Defaults to current directory.
    """
    try:
        entries = os.listdir(directory)
        result = []
        for entry in sorted(entries):
            full_path = os.path.join(directory, entry)
            if os.path.isdir(full_path):
                result.append(f"📁 {entry}/")
            else:
                size = os.path.getsize(full_path)
                result.append(f"📄 {entry} ({size} bytes)")
        return "\n".join(result) if result else "Empty directory"
    except FileNotFoundError:
        return f"Error: Directory not found: {directory}"
    except Exception as e:
        return f"Error listing directory: {e}"


BACKGROUND_PATTERNS = (
    "uvicorn", "gunicorn", "flask run", "django", "manage.py runserver",
    "npm start", "npm run dev", "npx ", "next dev", "next start",
    "vite", "webpack serve", "ng serve",
    "node server", "nodemon",
    "docker compose up", "docker-compose up",
    "celery worker", "celery beat",
    "redis-server", "mongod", "postgres",
    "streamlit run", "gradio",
    "http-server", "live-server", "serve ",
)


def _is_long_running(command: str) -> bool:
    """Detect commands that start servers or long-running processes."""
    cmd = command.strip()
    # Explicit background indicator
    if cmd.endswith("&"):
        return True
    return any(pattern in cmd for pattern in BACKGROUND_PATTERNS)


def run_command(command: str, timeout: int = 120, working_directory: str = "") -> str:
    """Execute a shell command and return its output. Use this for any system operation:
    git commands, running tests, installing packages, building projects, checking processes, etc.

    Args:
        command: The shell command to execute (e.g. 'git status', 'python -m pytest', 'npm install').
        timeout: Max seconds to wait for the command to finish. Defaults to 120.
        working_directory: Directory to run the command in. Defaults to current working directory.
    """
    cwd = working_directory or os.getcwd()

    # Long-running commands: start, capture initial output for a few seconds, then detach
    if _is_long_running(command):
        import threading
        import time

        startup_seconds = 5
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
            )

            # Read stdout in a thread so we don't block on Windows
            lines: list[str] = []
            stop_event = threading.Event()

            def _reader():
                while not stop_event.is_set():
                    try:
                        line = process.stdout.readline()
                        if line:
                            lines.append(line.rstrip())
                        elif process.poll() is not None:
                            break
                    except Exception:
                        break

            reader_thread = threading.Thread(target=_reader, daemon=True)
            reader_thread.start()

            # Wait for startup output or early exit
            time.sleep(startup_seconds)
            stop_event.set()
            reader_thread.join(timeout=1)

            exit_code = process.poll()
            output = "\n".join(lines) if lines else "(no output yet)"

            if exit_code is not None:
                # Process already finished (likely an error)
                return f"Process exited immediately (code {exit_code}):\n{output}"

            return (
                f"Process running in background (PID {process.pid}).\n"
                f"Initial output ({startup_seconds}s):\n{output}"
            )
        except Exception as e:
            return f"Error starting background process: {e}"

    try:
        process = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=cwd,
        )
        stdout, stderr = process.communicate(timeout=timeout)

        parts = []
        if stdout:
            parts.append(stdout)
        if stderr:
            parts.append(f"STDERR:\n{stderr}")
        if process.returncode != 0:
            parts.append(f"Exit code: {process.returncode}")

        return "\n".join(parts).strip() or "(no output)"
    except subprocess.TimeoutExpired:
        # Capture any partial output before killing
        process.kill()
        stdout, stderr = process.communicate()
        partial = (stdout or "") + (stderr or "")
        partial = partial.strip()
        msg = f"Error: Command timed out after {timeout} seconds."
        if partial:
            # Show last 20 lines so the agent can diagnose
            tail = "\n".join(partial.splitlines()[-20:])
            msg += f"\nLast output:\n{tail}"
        msg += "\nHint: if this is a server/long-running process, it will be detected and run in background automatically."
        return msg
    except Exception as e:
        return f"Error running command: {e}"


SAFE_COMMAND_PREFIXES = (
    # File/directory inspection
    "ls", "dir", "find", "tree", "cat", "head", "tail", "less", "more", "wc",
    "file", "stat", "du", "df",
    # Search
    "grep", "rg", "ag", "ack",
    # Git read-only
    "git status", "git log", "git diff", "git show", "git branch", "git tag",
    "git remote", "git stash list", "git blame", "git shortlog",
    # System info / navigation
    "cd", "echo", "pwd", "whoami", "which", "where", "type", "env", "printenv",
    "uname", "hostname", "ps", "top", "free", "uptime",
    # Language versions
    "python --version", "python3 --version", "node --version", "npm --version",
    "cargo --version", "go version", "java --version", "uv --version",
    # Sort/filter (typically piped)
    "sort", "uniq", "cut", "tr", "awk", "sed -n", "jq",
)


def _shell_split(command: str, separators: tuple[str, ...]) -> list[str] | None:
    """Split command by shell operators, respecting quotes.

    Returns list of parts if any separator found, None otherwise.
    """
    parts = []
    current = []
    in_single = False
    in_double = False
    i = 0
    chars = command
    while i < len(chars):
        c = chars[i]
        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
        elif c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
        elif not in_single and not in_double:
            matched = False
            for sep in separators:
                if chars[i:i+len(sep)] == sep:
                    parts.append("".join(current).strip())
                    current = []
                    i += len(sep)
                    matched = True
                    break
            if matched:
                continue
            current.append(c)
        else:
            current.append(c)
        i += 1
    if parts:  # at least one separator was found
        parts.append("".join(current).strip())
        return [p for p in parts if p]
    return None


def _is_safe_command(command: str) -> bool:
    """Check if a command is read-only and safe to run without permission."""
    cmd = command.strip()
    # Handle chained commands (&&, ;): safe only if ALL parts are safe
    parts = _shell_split(cmd, ("&&", ";"))
    if parts:
        return all(_is_safe_command(p) for p in parts)
    # Handle piped commands: safe only if ALL parts are safe
    parts = _shell_split(cmd, ("|",))
    if parts:
        return all(_is_safe_command(p) for p in parts)
    return any(cmd == prefix or cmd.startswith(prefix + " ") for prefix in SAFE_COMMAND_PREFIXES)


def bash(command: str, timeout: int = 120, working_directory: str = "") -> str:
    """Execute a bash command. This is your primary tool for interacting with the system.
    Use it for:
    - Running tests: 'python -m pytest tests/'
    - Git operations: 'git status', 'git diff', 'git add', 'git commit'
    - Installing packages: 'pip install', 'npm install', 'uv add'
    - Building projects: 'make', 'cargo build', 'go build'
    - Checking system state: 'ls', 'ps', 'env', 'which'
    - Any other shell command

    Args:
        command: The bash command to execute.
        timeout: Max seconds to wait. Defaults to 120.
        working_directory: Directory to run in. Defaults to current working directory.
    """
    cwd = working_directory or os.getcwd()
    if not _is_safe_command(command):
        cmd_display = Group(
            Syntax(command, "bash", theme="monokai"),
            Text(f"cwd: {cwd}", style="dim"),
        )
        if not _ask_permission("Bash Command", cmd_display):
            return f"Permission denied: {command}"
    return run_command(command, timeout=timeout, working_directory=working_directory)


class _HTMLToText(html.parser.HTMLParser):
    """Minimal HTML-to-text converter — no external dependencies."""

    SKIP_TAGS = {"script", "style", "svg", "noscript", "head"}
    BLOCK_TAGS = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6",
                  "li", "tr", "blockquote", "pre", "section", "article", "header", "footer"}

    def __init__(self):
        super().__init__()
        self._pieces: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS and not self._skip_depth:
            self._pieces.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self.BLOCK_TAGS and not self._skip_depth:
            self._pieces.append("\n")

    def handle_data(self, data):
        if not self._skip_depth:
            self._pieces.append(data)

    def get_text(self) -> str:
        raw = "".join(self._pieces)
        # Collapse whitespace within lines, preserve line breaks
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        # Collapse multiple blank lines
        text = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
        return text.strip()


def _html_to_text(html_content: str) -> str:
    parser = _HTMLToText()
    parser.feed(html_content)
    return parser.get_text()


def web_fetch(url: str, max_chars: int = 40000) -> str:
    """Fetch a URL and return its content as readable text.

    Use this to read web pages, GitHub repos/issues/PRs, documentation,
    API responses, or any publicly accessible URL.

    Args:
        url: The URL to fetch.
        max_chars: Maximum characters to return (default 40000) to avoid overwhelming context.
    """
    try:
        with httpx.Client(follow_redirects=True, timeout=30) as client:
            resp = client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (compatible; arc-agent/0.1)",
                "Accept": "text/html,application/json,text/plain,*/*",
            })
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        return f"HTTP error {e.response.status_code}: {e.response.reason_phrase}"
    except httpx.RequestError as e:
        return f"Request error: {e}"

    content_type = resp.headers.get("content-type", "")
    body = resp.text

    if "json" in content_type:
        # JSON — return as-is (already readable)
        text = body
    elif "html" in content_type:
        text = _html_to_text(body)
    else:
        # Plain text or other
        text = body

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n\n... [truncated at {max_chars} chars]"
    return text


# All tools as a list for easy import
ALL_TOOLS = [
    read_file,
    write_file,
    write_files,
    edit_file,
    edit_files,
    glob_search,
    grep_search,
    list_directory,
    bash,
    web_fetch,
]
