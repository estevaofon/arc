"""Auto-memory extraction — Tier 2 #4.

Per-project durable facts extracted from user↔assistant turns and loaded
back into the system prompt on subsequent sessions.

Components:
- ``store``     — disk layout, read/write of MEMORY.md + individual files
- ``extractor`` — async extraction triggered by the ``turn.end`` hook
- ``loader``    — renders MEMORY.md into the system prompt at startup

Storage layout:

    ~/.aru/projects/<path-encoded>/memory/
      ├── MEMORY.md              # one-line-per-memory index
      ├── feedback_*.md          # one file per memory, YAML frontmatter + body
      └── user_*.md

``<path-encoded>`` mirrors Claude Code's scheme: every non-alphanumeric
character in ``abspath(project_root)`` becomes a dash. Example::

    D:\\OneDrive\\python_projects\\aru -> D--OneDrive-python-projects-aru

The directory is created lazily on the first ``write_memory`` call, so a
project that never writes a memory never leaves an empty folder behind.

Config (aru.json):

    {
      "memory": {
        "auto_extract": true,                # default false — opt-in
        "model_ref": "anthropic/claude-haiku-4-5",
        "min_turn_tokens": 500
      }
    }
"""

from aru.memory.loader import load_memory_index, memory_section_for_prompt
from aru.memory.store import (
    MemoryEntry,
    delete_memory,
    list_memories,
    memory_dir_for_project,
    read_memory,
    search_memories,
    write_memory,
)

__all__ = [
    "MemoryEntry",
    "delete_memory",
    "list_memories",
    "load_memory_index",
    "memory_dir_for_project",
    "memory_section_for_prompt",
    "read_memory",
    "search_memories",
    "write_memory",
]
