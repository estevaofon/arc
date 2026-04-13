"""Gitignore-aware file filtering for codebase operations."""

from __future__ import annotations

import os
import threading
from typing import Iterator

import pathspec


def normalize_path(path: str) -> str:
    """Convert backslashes to forward slashes and remove trailing slashes."""
    return path.replace("\\", "/").rstrip("/")

# Hardcoded fallback patterns (always excluded even without .gitignore)
_FALLBACK_PATTERNS = [
    ".git",
    "node_modules",
    "__pycache__",
    "venv",
    ".venv",
    ".aru",
    "*.pyc",
    "*.pyo",
]

# Cache: {(root_dir, gitignore_mtime): PathSpec}
_cache: dict[tuple[str, float], pathspec.PathSpec] = {}

# File-list cache so repeated glob/grep/rank calls don't re-walk the FS.
# Key: absolute directory; value: (gitignore_mtime, [(dirpath, dirs, files), ...]).
_walk_cache: dict[str, tuple[float, list[tuple[str, list[str], list[str]]]]] = {}
_walk_cache_lock = threading.Lock()


def invalidate_walk_cache(directory: str | None = None) -> None:
    """Drop cached walk results.

    Called after file mutations so subsequent walks see fresh state.
    """
    with _walk_cache_lock:
        if directory is None:
            _walk_cache.clear()
        else:
            abs_dir = os.path.abspath(directory)
            for key in list(_walk_cache):
                if key == abs_dir or key.startswith(abs_dir + os.sep):
                    _walk_cache.pop(key, None)


def _find_git_root(start: str) -> str | None:
    """Walk up from start directory to find the git root (directory containing .git)."""
    current = os.path.abspath(start)
    while True:
        if os.path.isdir(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def load_gitignore(root_dir: str) -> pathspec.PathSpec:
    """Parse .gitignore from root_dir combined with hardcoded fallback patterns.

    Results are cached by root_dir and .gitignore mtime.
    """
    root_dir = os.path.abspath(root_dir)
    gitignore_path = os.path.join(root_dir, ".gitignore")

    mtime = 0.0
    if os.path.isfile(gitignore_path):
        mtime = os.path.getmtime(gitignore_path)

    cache_key = (root_dir, mtime)
    if cache_key in _cache:
        return _cache[cache_key]

    # Clear old entries for this root_dir
    _cache.pop(next((k for k in _cache if k[0] == root_dir), (None, None)), None)

    patterns = list(_FALLBACK_PATTERNS)
    if os.path.isfile(gitignore_path):
        with open(gitignore_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    patterns.append(line)

    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    _cache[cache_key] = spec
    return spec


def is_ignored(path: str, root_dir: str) -> bool:
    """Check if a relative path should be ignored based on .gitignore rules.

    Args:
        path: Relative path to check (forward slashes preferred).
        root_dir: Project root directory containing .gitignore.
    """
    spec = load_gitignore(root_dir)
    # Normalize to forward slashes for pathspec
    normalized = path.replace("\\", "/")
    return spec.match_file(normalized)


def _build_walk_entries(directory: str) -> list[tuple[str, list[str], list[str]]]:
    """Compute the filtered (dirpath, dirs, files) list without caching."""
    root_dir = _find_git_root(directory) or directory
    spec = load_gitignore(root_dir)

    entries: list[tuple[str, list[str], list[str]]] = []
    for dirpath, dirs, files in os.walk(directory):
        dirs[:] = [
            d for d in dirs
            if not spec.match_file(os.path.relpath(os.path.join(dirpath, d), root_dir).replace("\\", "/") + "/")
        ]
        filtered_files = [
            f for f in files
            if not spec.match_file(os.path.relpath(os.path.join(dirpath, f), root_dir).replace("\\", "/"))
        ]
        entries.append((dirpath, list(dirs), filtered_files))
    return entries


def _gitignore_mtime(root_dir: str) -> float:
    gitignore_path = os.path.join(root_dir, ".gitignore")
    try:
        return os.path.getmtime(gitignore_path)
    except OSError:
        return 0.0


def walk_filtered(directory: str) -> Iterator[tuple[str, list[str], list[str]]]:
    """Walk directory tree, filtering out gitignored files and directories.

    Drop-in replacement for os.walk() that respects .gitignore rules.
    Results are cached per directory and invalidated on file mutations via
    ``invalidate_walk_cache`` — see codebase.py's mutation hooks.
    """
    directory = os.path.abspath(directory)
    root_dir = _find_git_root(directory) or directory
    current_mtime = _gitignore_mtime(root_dir)

    with _walk_cache_lock:
        cached = _walk_cache.get(directory)
        if cached is not None and cached[0] == current_mtime:
            entries = cached[1]
        else:
            entries = None

    if entries is None:
        entries = _build_walk_entries(directory)
        with _walk_cache_lock:
            _walk_cache[directory] = (current_mtime, entries)

    for dirpath, dirs, files in entries:
        # Hand out shallow copies so caller mutations (e.g. dirs.clear())
        # don't corrupt the cache. Callers that relied on dirs.clear() for
        # pruning must instead filter by depth themselves — we can't stop
        # iteration over a precomputed list.
        yield dirpath, list(dirs), list(files)


def list_project_files(directory: str) -> list[str]:
    """Flat list of every non-ignored file under *directory* (absolute paths).

    Uses the same cache as walk_filtered.
    """
    results: list[str] = []
    for dirpath, _dirs, files in walk_filtered(directory):
        for f in files:
            results.append(os.path.join(dirpath, f))
    return results
