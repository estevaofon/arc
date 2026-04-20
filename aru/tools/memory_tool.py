"""Agent-facing memory query tool — Tier 3 #3.

Exposes the per-project memory store (written by the Tier 2 auto-extractor)
as a read-only tool. Two modes:

- ``memory_search(slug="...")``    → returns the full body of one memory
- ``memory_search(query="...")``   → keyword substring search over
                                     name / description / body, returns a
                                     ranked list with 200-char previews
- ``memory_search()``              → summary stats by type

The system prompt already receives ``MEMORY.md`` as an index at startup
(Tier 2 #4). This tool is the complement for *reading* a specific body
or searching when the index alone isn't enough.
"""

from __future__ import annotations

import os
from collections import Counter

from aru.memory.store import (
    VALID_MEMORY_TYPES,
    list_memories,
    read_memory,
    search_memories,
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
