"""Multi-factor file relevance ranking for task-driven context selection."""

import fnmatch
import os
import re
from concurrent.futures import ThreadPoolExecutor

from aru.tools.gitignore import walk_filtered

# Weights for each ranking signal (sum to 1.0)
WEIGHT_NAME = 0.50
WEIGHT_STRUCTURAL = 0.30
WEIGHT_RECENCY = 0.20


def _get_project_files(root_dir: str) -> list[str]:
    """Get all project files using gitignore-aware walk."""
    files = []
    for dirpath, _, filenames in walk_filtered(root_dir):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(filepath, root_dir).replace("\\", "/")
            files.append(rel_path)
    return files


def _collect_mtimes(root_dir: str, files: list[str]) -> dict[str, float]:
    """Return {rel_path: mtime} using a single scandir pass per directory.

    Previously we called ``os.path.getmtime`` per file, which is one syscall
    per file. ``os.scandir`` batches the dir listing with stat info so we
    only pay one syscall per directory for modest cost.
    """
    mtimes: dict[str, float] = {}
    # Group files by parent directory for scandir access
    groups: dict[str, set[str]] = {}
    for rel in files:
        parent = os.path.dirname(rel)
        groups.setdefault(parent, set()).add(os.path.basename(rel))

    for parent, names in groups.items():
        abs_parent = os.path.join(root_dir, parent) if parent else root_dir
        try:
            with os.scandir(abs_parent) as it:
                for entry in it:
                    if entry.name not in names:
                        continue
                    try:
                        st = entry.stat()
                        rel_path = (
                            f"{parent}/{entry.name}" if parent else entry.name
                        ).replace("\\", "/")
                        mtimes[rel_path] = st.st_mtime
                    except OSError:
                        continue
        except OSError:
            continue
    return mtimes


def _score_name_match(file_path: str, keywords: list[str]) -> float:
    """Score based on how many task keywords appear in the file path/name."""
    if not keywords:
        return 0.0

    path_lower = file_path.lower()
    # Split path into components for matching
    path_parts = re.split(r"[/\\_.\-]", path_lower)

    matches = 0
    for keyword in keywords:
        kw = keyword.lower()
        if len(kw) < 3:  # Skip very short words
            continue
        # Exact match in path component
        if kw in path_parts:
            matches += 2
        # Partial match in full path
        elif kw in path_lower:
            matches += 1
        # Check if any path component is a substring of the keyword (e.g., "auth" in "authentication")
        else:
            for part in path_parts:
                if len(part) >= 3 and part in kw:
                    matches += 1.5  # Higher than partial match, lower than exact
                    break

    return min(matches / max(len(keywords), 1), 1.0)


def _extract_keywords(task: str) -> list[str]:
    """Extract meaningful keywords from a task description."""
    # Common stop words to filter out
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "can", "shall", "to", "of", "in", "for",
        "on", "with", "at", "by", "from", "as", "into", "through", "during",
        "before", "after", "above", "below", "between", "out", "off", "over",
        "under", "again", "further", "then", "once", "here", "there", "when",
        "where", "why", "how", "all", "each", "every", "both", "few", "more",
        "most", "other", "some", "such", "no", "nor", "not", "only", "own",
        "same", "so", "than", "too", "very", "just", "but", "and", "or",
        "if", "it", "its", "this", "that", "these", "those", "i", "me", "my",
        "we", "our", "you", "your", "he", "she", "they", "them", "what",
        "which", "who", "whom", "add", "create", "make", "build", "implement",
        "fix", "update", "change", "modify", "remove", "delete", "get", "set",
        "use", "new", "file", "files", "code", "function", "method",
    }

    # Tokenize and filter
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]*", task)
    keywords = [w for w in words if w.lower() not in stop_words and len(w) >= 3]
    return keywords


def _score_recency(file_path: str, root_dir: str, max_age_days: float = 30.0) -> float:
    """Score based on how recently the file was modified (0-1, 1 = most recent).

    Kept as a standalone helper for tests; rank_files uses a batched path
    (``_collect_mtimes``) to avoid one syscall per file.
    """
    try:
        mtime = os.path.getmtime(os.path.join(root_dir, file_path))
        import time
        age_seconds = time.time() - mtime
        age_days = age_seconds / 86400
        if age_days <= 0:
            return 1.0
        if age_days >= max_age_days:
            return 0.0
        return 1.0 - (age_days / max_age_days)
    except OSError:
        return 0.0


def _recency_from_mtime(mtime: float, max_age_days: float = 30.0) -> float:
    import time
    age_days = (time.time() - mtime) / 86400
    if age_days <= 0:
        return 1.0
    if age_days >= max_age_days:
        return 0.0
    return 1.0 - (age_days / max_age_days)


def _read_file_content(full_path: str) -> str | None:
    """Read a file as text, returning None on failure. Used by parallel pool."""
    try:
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return None


def _get_structural_scores(top_files: list[str], root_dir: str) -> dict[str, float]:
    """Boost files that are dependencies of already-relevant files.

    Reads the top-N candidates in parallel via a small thread pool so we
    don't serialize on file I/O latency.
    """
    try:
        from aru.tools.ast_tools import _resolve_import_to_file, _find_project_root  # noqa: F401
    except ImportError:
        return {}

    candidates = [
        (fp, os.path.join(root_dir, fp))
        for fp in top_files[:5]
        if os.path.isfile(os.path.join(root_dir, fp))
    ]
    if not candidates:
        return {}

    with ThreadPoolExecutor(max_workers=min(5, len(candidates))) as pool:
        contents = list(pool.map(_read_file_content, [c[1] for c in candidates]))

    dep_counts: dict[str, int] = {}
    for (_fp, _full), content in zip(candidates, contents):
        if not content:
            continue
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped.startswith("import ") or stripped.startswith("from "):
                resolved = _resolve_import_to_file(stripped, root_dir)
                if resolved:
                    normalized = resolved.replace("\\", "/")
                    dep_counts[normalized] = dep_counts.get(normalized, 0) + 1

    if not dep_counts:
        return {}

    max_count = max(dep_counts.values())
    return {k: v / max_count for k, v in dep_counts.items()}


def rank_files(task: str, top_k: int = 15) -> str:
    """Rank project files by relevance to a given task description.

    Uses multiple signals to determine which files are most relevant:
    - Filename/path keyword matching
    - Structural dependencies (files imported by relevant files)
    - Modification recency

    Use this as a first step when starting a new task to identify which files to read.

    Args:
        task: Natural language description of the task (e.g. "add authentication to the CLI").
        top_k: Maximum number of files to return. Defaults to 15.
    """
    # Tier 3 #2: respect the agent scope's cwd so explorer subagents in a
    # worktree rank files from that worktree, not the process root.
    from aru.runtime import get_cwd as _get_cwd
    root_dir = _get_cwd()
    all_files = _get_project_files(root_dir)

    if not all_files:
        return "No files found in the project."

    keywords = _extract_keywords(task)

    # Signal 1: Name match scores
    name_scores = {f: _score_name_match(f, keywords) for f in all_files}

    # Signal 2: Recency scores — batch mtime lookup via scandir (one syscall
    # per directory instead of per file).
    mtimes = _collect_mtimes(root_dir, all_files)
    recency_scores = {
        f: _recency_from_mtime(mtimes[f]) if f in mtimes else 0.0 for f in all_files
    }

    # Preliminary ranking (without structural) to find top files for dependency tracing
    preliminary_scores = {}
    for f in all_files:
        score = (
            WEIGHT_NAME * name_scores.get(f, 0.0)
            + WEIGHT_RECENCY * recency_scores.get(f, 0.0)
        )
        preliminary_scores[f] = score

    # Signal 3: Structural scores (based on top preliminary results)
    top_preliminary = sorted(preliminary_scores, key=preliminary_scores.get, reverse=True)[:10]
    structural_scores = _get_structural_scores(top_preliminary, root_dir)

    # Final combined scores
    final_scores: dict[str, tuple[float, list[str]]] = {}
    for f in all_files:
        reasons = []
        name = name_scores.get(f, 0.0)
        structural = structural_scores.get(f, 0.0)
        recency = recency_scores.get(f, 0.0)

        score = (
            WEIGHT_NAME * name
            + WEIGHT_STRUCTURAL * structural
            + WEIGHT_RECENCY * recency
        )

        # Build reason strings
        if name > 0.3:
            reasons.append("name match")
        if structural > 0:
            reasons.append("dependency of top files")
        if recency > 0.7:
            reasons.append("recently modified")

        if score > 0:
            final_scores[f] = (score, reasons)

    # Sort and take top_k
    ranked = sorted(final_scores.items(), key=lambda x: x[1][0], reverse=True)[:top_k]

    if not ranked:
        return f"No files found with relevance to: {task}"

    # Normalize scores to 0-1 based on top score
    max_score = ranked[0][1][0] if ranked else 1.0
    if max_score == 0:
        max_score = 1.0

    # Format output
    lines = [f"Files ranked by relevance to: \"{task}\"\n"]
    lines.append("Ranking mode: name + structural + recency\n")

    for i, (file_path, (score, reasons)) in enumerate(ranked, 1):
        normalized_score = score / max_score
        reason_str = " + ".join(reasons) if reasons else "low signal"
        lines.append(f"  {i:2d}. {file_path} ({normalized_score:.2f}) — {reason_str}")

    return "\n".join(lines)
