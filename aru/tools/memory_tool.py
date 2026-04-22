"""Agent-facing memory tools — Tier 3 #3.

Exposes the per-project memory store as a pair of tools:

- ``memory_search(slug="...")``    → returns the full body of one memory
- ``memory_search(query="...")``   → keyword substring search over
                                     name / description / body, returns a
                                     ranked list with 200-char previews
- ``memory_search()``              → summary stats by type
- ``memory_write(name, body, ...)`` → persist a new memory explicitly,
                                      without waiting for the turn.end
                                      extractor

The system prompt already receives ``MEMORY.md`` as an index at startup
(Tier 2 #4). ``memory_search`` is the read complement; ``memory_write``
lets the agent honour direct user requests like "save X to memory" that
would otherwise fall through the extractor's threshold.
"""

from __future__ import annotations

import os
from collections import Counter

from aru.memory.store import (
    MemoryEntry,
    VALID_MEMORY_TYPES,
    list_memories,
    read_memory,
    search_memories,
    write_memory,
)
from aru.runtime import get_ctx


def _project_root() -> str:
    try:
        ctx = get_ctx()
    except LookupError:
        return os.getcwd()
    session = getattr(ctx, "session", None)
    if session is not None:
        root = getattr(session, "project_root", None)
        if root:
            return root
    return os.getcwd()


def memory_search(query: str = "", slug: str = "") -> str:
    """Query or read this project's auto-memory.

    Args:
        query: Case-insensitive substring. When provided, returns a ranked
            list (name matches first, then description, then body) with
            200-char body previews. Empty string triggers summary mode
            unless ``slug`` is set.
        slug: When provided, returns the full body of the memory with that
            exact slug (from the index, e.g. ``user_prefer_pytest``).
            Takes precedence over ``query``.
    """
    project_root = _project_root()
    slug = (slug or "").strip()
    query = (query or "").strip()

    if slug:
        entry = read_memory(project_root, slug)
        if entry is None:
            return (
                f"No memory with slug {slug!r}. "
                f"Use memory_search() to list all slugs."
            )
        return (
            f"name: {entry.name}\n"
            f"description: {entry.description}\n"
            f"type: {entry.type}\n"
            f"slug: {entry.slug}\n\n"
            f"{entry.body}"
        )

    if not query:
        # Summary mode — lets the agent see whether it's worth digging deeper
        entries = list_memories(project_root)
        if not entries:
            return (
                "No memories stored for this project. "
                "Enable auto-extraction via "
                '"memory": {"auto_extract": true} in aru.json.'
            )
        counts = Counter(e.type for e in entries)
        type_summary = ", ".join(
            f"{counts.get(t, 0)} {t}" for t in sorted(VALID_MEMORY_TYPES)
        )
        return (
            f"{len(entries)} memories in this project "
            f"({type_summary}). Call memory_search(query=\"...\") "
            f"to filter, or memory_search(slug=\"...\") for a specific body."
        )

    matches = search_memories(project_root, query)
    if not matches:
        return f"No memories matching query {query!r}."

    lines = [f"{len(matches)} match(es) for {query!r}:"]
    for e in matches:
        preview = (e.body or "").strip().replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:200].rstrip() + "…"
        lines.append(
            f"\n- [{e.slug}] ({e.type}) {e.name}"
            f"\n  {e.description}"
            f"\n  body: {preview}"
        )
    return "\n".join(lines)


def memory_write(name: str, body: str, type: str = "user",
                 description: str = "") -> str:
    """Persist a durable memory for this project across future sessions.

    Use when the user explicitly asks to save / remember / "lembra" / "salva"
    something that should survive session boundaries. Pick the type carefully:

    - ``user``      — user's persistent preferences or workflow rules
                      ("prefer pytest", "always type hints")
    - ``feedback``  — corrections the user gave ("don't mock the DB, got burned")
    - ``project``   — project-level state / decisions / deadlines / incidents
    - ``reference`` — pointers to external systems (dashboards, tickets, docs)

    Do NOT save:
    - Code patterns or anything derivable from reading the repo
    - Ephemeral conversation state
    - Duplicates of what is already in the memory index

    Args:
        name: Short title (under 60 chars). Used as the memory's display name
            and as the base for its filename slug.
        body: The fact to remember (under 400 chars). Prefer a single
            declarative sentence; future sessions see this verbatim.
        type: One of ``user`` / ``feedback`` / ``project`` / ``reference``.
            Defaults to ``user``.
        description: Optional one-line summary for the MEMORY.md index
            (under 100 chars). If empty, defaults to ``name``.
    """
    mtype = (type or "user").strip().lower()
    if mtype not in VALID_MEMORY_TYPES:
        return (
            f"Invalid memory type {mtype!r}. Must be one of "
            f"{sorted(VALID_MEMORY_TYPES)}."
        )
    name_clean = (name or "").strip()[:60]
    body_clean = (body or "").strip()[:400]
    desc_clean = (description or "").strip()[:100] or name_clean
    if not name_clean or not body_clean:
        return "memory_write requires both `name` and `body`."

    entry = MemoryEntry(
        name=name_clean,
        description=desc_clean,
        type=mtype,
        body=body_clean,
    )
    persisted = write_memory(_project_root(), entry)
    return (
        f"Saved memory '{persisted.slug}' ({persisted.type}): "
        f"{persisted.name}"
    )
