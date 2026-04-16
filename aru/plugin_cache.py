"""Plugin caching and installation system.

Inspired by OpenCode's plugin architecture (packages/opencode/src/plugin/).
Enables installing plugins from git URLs or local paths, caching them under
~/.aru/plugins/cache/packages/, and making their skills/agents/tools/plugins
discoverable by the existing Aru discovery pipelines.

Flow:
  install(spec) -> parse_spec -> git_clone / copy_local
               -> read_manifest -> check_compatibility
               -> update_meta -> patch_config

Discovery integration:
  get_cached_plugin_roots() returns list[Path] injected into:
    - config._discover_skills
    - config._discover_agents
    - plugins.custom_tools._default_search_roots
    - plugins.manager._default_plugin_roots

Thread/process safety via file locks in ~/.aru/plugins/locks/.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("aru.plugin_cache")


ARU_HOME = Path.home() / ".aru"
PLUGINS_ROOT = ARU_HOME / "plugins"
CACHE_DIR = PLUGINS_ROOT / "cache" / "packages"
META_PATH = PLUGINS_ROOT / "meta.json"
LOCK_DIR = PLUGINS_ROOT / "locks"
LOCK_TTL_SECONDS = 3600  # Stale lock removed after 1h

GITHUB_SHORTHAND_RE = re.compile(r"^github:([^/]+)/([^@]+)(?:@(.+))?$")
GIT_URL_RE = re.compile(r"^(git\+)?(https?|ssh|git)://")
FILE_URL_PREFIX = "file://"


@dataclass
class PluginEntry:
    """Metadata for an installed plugin (adapted from OpenCode PluginMeta.Entry)."""
    id: str
    source: str  # "git" | "file"
    spec: str
    target: str
    requested: str | None = None
    version: str | None = None
    first_time: int = 0
    last_time: int = 0
    time_changed: int = 0
    load_count: int = 0
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InstallResult:
    """Result of a plugin install / update operation."""
    ok: bool
    target: Path | None = None
    name: str | None = None
    version: str | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    state: str = "first"  # "first" | "updated" | "same"
    error: str | None = None

    @property
    def provides(self) -> dict[str, int]:
        """Quick summary of what the installed plugin provides."""
        if not self.target:
            return {}
        counts = {}
        for kind in ("skills", "agents", "tools", "plugins"):
            d = self.target / kind
            if not d.is_dir():
                continue
            if kind == "skills":
                counts[kind] = sum(1 for p in d.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())
            elif kind == "agents":
                counts[kind] = sum(1 for p in d.iterdir() if p.suffix == ".md")
            else:
                counts[kind] = sum(1 for p in d.iterdir() if p.suffix == ".py" and not p.name.startswith("_"))
        return counts


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------
def get_cache_dir() -> Path:
    """Return the cache root, creating it if missing."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR


def get_lock_dir() -> Path:
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    return LOCK_DIR


def get_cached_plugin_roots() -> list[Path]:
    """Return list of directories to inject into Aru's discovery search roots.

    Each entry is a path like ~/.aru/plugins/cache/packages/<plugin-name>/
    that may contain skills/, agents/, tools/, plugins/ subdirectories.
    """
    if not CACHE_DIR.is_dir():
        return []
    roots: list[Path] = []
    for entry in sorted(CACHE_DIR.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name.startswith("."):
            continue
        roots.append(entry)
    return roots


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------
def parse_spec(spec: str) -> tuple[str, str, str | None]:
    """Parse a plugin spec into (source_type, normalized_url, ref).

    Returns:
        (source_type, normalized, ref)
        - source_type: "git" | "file"
        - normalized: URL (for git) or absolute path (for file)
        - ref: git ref (tag/branch/sha) if specified, else None
    """
    spec = spec.strip()

    # github shorthand: github:user/repo[@ref]
    m = GITHUB_SHORTHAND_RE.match(spec)
    if m:
        user, repo, ref = m.group(1), m.group(2), m.group(3)
        return "git", f"https://github.com/{user}/{repo}.git", ref

    # git+https://... or https://...git
    if spec.startswith("git+"):
        url = spec[len("git+"):]
        ref = None
        if "@" in url and not url.startswith("ssh://"):
            # Split ref only after the last path segment
            base, _, maybe_ref = url.rpartition("@")
            if not base.endswith(":") and "://" in base:
                url, ref = base, maybe_ref
        return "git", url, ref

    if GIT_URL_RE.match(spec) and (spec.endswith(".git") or "/tree/" in spec):
        url = spec
        ref = None
        if url.endswith(".git") and "@" in url:
            base, _, maybe_ref = url.rpartition("@")
            if not base.endswith(":") and "://" in base:
                url, ref = base, maybe_ref
        return "git", url, ref

    # file:// or local path
    if spec.startswith(FILE_URL_PREFIX):
        path = spec[len(FILE_URL_PREFIX):]
        # Handle file:///C:/... on Windows
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
        return "file", str(Path(path).resolve()), None

    p = Path(spec)
    if p.is_absolute() or spec.startswith("./") or spec.startswith("../") or p.exists():
        return "file", str(p.resolve()), None

    # Bare name — treat as github shorthand requiring explicit "github:" prefix
    raise ValueError(
        f"Cannot parse plugin spec: {spec!r}. "
        "Expected: github:user/repo, git+https://..., file://..., or local path."
    )


def infer_name(spec: str, source: str, normalized: str) -> str:
    """Infer a plugin name from the spec.

    - github:user/repo -> repo
    - git+https://host/path/to/repo.git -> repo
    - file:///path/to/plugin-dir -> plugin-dir
    """
    if source == "git":
        path = normalized.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return path.rsplit("/", 1)[-1]
    # file
    return Path(normalized).name


# ---------------------------------------------------------------------------
# File lock (simple TTL-based)
# ---------------------------------------------------------------------------
class _FileLock:
    """Simple TTL-based file lock. Cross-platform (no fcntl/msvcrt)."""

    def __init__(self, name: str):
        self.path = get_lock_dir() / f"{name}.lock"

    def acquire(self, timeout: float = 30.0) -> None:
        deadline = time.time() + timeout
        while True:
            if self._try_create():
                return
            # Check for stale lock
            try:
                age = time.time() - self.path.stat().st_mtime
                if age > LOCK_TTL_SECONDS:
                    logger.warning("Removing stale lock: %s (age=%.0fs)", self.path, age)
                    self.path.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.time() > deadline:
                raise TimeoutError(f"Could not acquire lock: {self.path}")
            time.sleep(0.2)

    def _try_create(self) -> bool:
        try:
            # O_EXCL ensures exclusive creation
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            return False

    def release(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *_):
        self.release()


# ---------------------------------------------------------------------------
# Manifest + compatibility
# ---------------------------------------------------------------------------
def read_manifest(target: Path) -> dict[str, Any]:
    """Read aru-plugin.json from the plugin root. Returns empty dict if missing."""
    manifest_path = target / "aru-plugin.json"
    if not manifest_path.is_file():
        return {}
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read manifest %s: %s", manifest_path, exc)
        return {}


def _parse_version_tuple(version: str) -> tuple[int, ...]:
    parts = []
    for seg in version.split("."):
        m = re.match(r"(\d+)", seg)
        if not m:
            break
        parts.append(int(m.group(1)))
    return tuple(parts) if parts else (0,)


def _satisfies(version: str, spec: str) -> bool:
    """Very small semver satisfier: supports >=, >, <=, <, ==, ~=, ^, or exact."""
    spec = spec.strip()
    if not spec or spec == "*":
        return True

    v = _parse_version_tuple(version)

    for op in (">=", "<=", "==", "~=", ">", "<"):
        if spec.startswith(op):
            target = _parse_version_tuple(spec[len(op):].strip())
            if op == ">=": return v >= target
            if op == "<=": return v <= target
            if op == "==": return v == target
            if op == ">": return v > target
            if op == "<": return v < target
            if op == "~=":
                # ~=1.2.3 -> >=1.2.3, <1.3.0
                if len(target) < 2:
                    return v >= target
                upper = target[:-2] + (target[-2] + 1,)
                return v >= target and v < upper
    if spec.startswith("^"):
        target = _parse_version_tuple(spec[1:].strip())
        if not target:
            return True
        upper = (target[0] + 1,)
        return v >= target and v < upper

    # Exact match
    return v == _parse_version_tuple(spec)


def check_compatibility(manifest: dict[str, Any], aru_version: str) -> None:
    """Verify engines.aru semver is satisfied by aru_version. Raises ValueError on mismatch."""
    engines = manifest.get("engines") or {}
    if not isinstance(engines, dict):
        return
    spec = engines.get("aru")
    if not spec:
        return
    if not _satisfies(aru_version, str(spec)):
        raise ValueError(
            f"Plugin requires aru {spec} but running {aru_version}"
        )


def fingerprint(target: Path) -> str:
    """Compute a stable fingerprint for change detection.

    Hashes file paths + sizes + mtimes for all tracked content.
    Cheap to compute, stable across runs.
    """
    if not target.is_dir():
        return ""
    h = hashlib.sha256()
    entries: list[tuple[str, int, int]] = []
    for p in target.rglob("*"):
        if ".git" in p.parts:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        rel = p.relative_to(target).as_posix()
        entries.append((rel, int(st.st_size), int(st.st_mtime)))
    for rel, size, mtime in sorted(entries):
        h.update(f"{rel}:{size}:{mtime}\n".encode())
    return "sha256:" + h.hexdigest()[:32]


# ---------------------------------------------------------------------------
# Meta persistence
# ---------------------------------------------------------------------------
def _load_meta() -> dict[str, dict[str, Any]]:
    if not META_PATH.is_file():
        return {}
    try:
        data = json.loads(META_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_meta(data: dict[str, dict[str, Any]]) -> None:
    META_PATH.parent.mkdir(parents=True, exist_ok=True)
    META_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _meta_entry_for(name: str) -> PluginEntry | None:
    store = _load_meta()
    raw = store.get(name)
    if not isinstance(raw, dict):
        return None
    try:
        return PluginEntry(**raw)
    except TypeError:
        return None


def _update_meta(entry: PluginEntry, state: str) -> None:
    store = _load_meta()
    now = int(time.time())
    existing = store.get(entry.id)

    if isinstance(existing, dict):
        entry.first_time = int(existing.get("first_time") or now)
        entry.load_count = int(existing.get("load_count") or 0)
        entry.time_changed = int(existing.get("time_changed") or now)
        if state == "updated":
            entry.time_changed = now
    else:
        entry.first_time = now
        entry.time_changed = now
        entry.load_count = 0

    entry.last_time = now
    store[entry.id] = entry.to_dict()
    _save_meta(store)


def list_installed() -> list[PluginEntry]:
    """Return list of installed plugins from meta.json."""
    store = _load_meta()
    out: list[PluginEntry] = []
    for name, raw in sorted(store.items()):
        if not isinstance(raw, dict):
            continue
        try:
            out.append(PluginEntry(**raw))
        except TypeError:
            continue
    return out


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------
def _check_git_available() -> None:
    try:
        subprocess.run(
            ["git", "--version"],
            capture_output=True, check=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            "git is required for plugin installation but was not found. "
            "Install git from https://git-scm.com/"
        ) from exc


def _on_rm_error(func, path, exc_info):
    """shutil.rmtree error handler: clear read-only bit and retry.

    Needed on Windows for .git/objects/pack/*.idx files which are created
    read-only by git clone and can't be unlinked without chmod +w first.
    """
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass  # best-effort; swallow so removal continues


def _rmtree_force(path: Path) -> None:
    """shutil.rmtree that handles read-only files (Windows .git quirk)."""
    shutil.rmtree(path, onerror=_on_rm_error)


def _git_clone(url: str, target: Path, ref: str | None = None) -> None:
    """Clone a git repository to target, optionally checking out ref."""
    _check_git_available()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        _rmtree_force(target)

    cmd = ["git", "clone", "--depth", "1"]
    if ref:
        cmd.extend(["--branch", ref])
    cmd.extend([url, str(target)])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"git clone failed: {result.stderr.strip()}")


def _git_pull(target: Path) -> None:
    """Run git pull in the target directory."""
    _check_git_available()
    result = subprocess.run(
        ["git", "-C", str(target), "pull", "--ff-only"],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git pull failed: {result.stderr.strip()}")


# ---------------------------------------------------------------------------
# Install / remove / update / list
# ---------------------------------------------------------------------------
def _get_aru_version() -> str:
    try:
        from aru import __version__  # type: ignore
        return str(__version__)
    except Exception:
        pass
    # Fallback: read from pyproject.toml
    try:
        project_root = Path(__file__).resolve().parent.parent
        pyproject = project_root / "pyproject.toml"
        if pyproject.is_file():
            for line in pyproject.read_text(encoding="utf-8").splitlines():
                m = re.match(r'^version\s*=\s*"([^"]+)"', line.strip())
                if m:
                    return m.group(1)
    except Exception:
        pass
    return "0.0.0"


def install(spec: str, name: str | None = None, force: bool = False) -> InstallResult:
    """Install a plugin from a spec string.

    Args:
        spec: Plugin spec (github:user/repo, git+https://..., file://..., or local path).
        name: Override the inferred plugin name.
        force: If True, reinstall even if already cached.

    Returns:
        InstallResult describing outcome.
    """
    try:
        source, normalized, ref = parse_spec(spec)
    except ValueError as exc:
        return InstallResult(ok=False, error=str(exc))

    plugin_name = name or infer_name(spec, source, normalized)
    target = get_cache_dir() / plugin_name
    existing_entry = _meta_entry_for(plugin_name)
    state = "first" if existing_entry is None else "updated"

    with _FileLock(plugin_name):
        try:
            if source == "git":
                if target.exists() and not force and existing_entry is not None:
                    state = "same"
                else:
                    _git_clone(normalized, target, ref)
            elif source == "file":
                src = Path(normalized)
                if not src.is_dir():
                    return InstallResult(
                        ok=False,
                        error=f"Local plugin path not found or not a directory: {src}",
                    )
                if target.exists():
                    _rmtree_force(target)
                shutil.copytree(src, target, ignore=shutil.ignore_patterns(".git"))
            else:
                return InstallResult(ok=False, error=f"Unsupported source: {source}")
        except Exception as exc:
            return InstallResult(ok=False, error=f"install failed: {exc}")

    manifest = read_manifest(target)
    aru_version = _get_aru_version()
    try:
        check_compatibility(manifest, aru_version)
    except ValueError as exc:
        # Rollback
        if target.exists():
            _rmtree_force(target)
        return InstallResult(ok=False, error=str(exc))

    fp = fingerprint(target)
    if existing_entry is not None and existing_entry.fingerprint == fp:
        state = "same"
    elif existing_entry is not None:
        state = "updated"

    entry = PluginEntry(
        id=plugin_name,
        source=source,
        spec=spec,
        target=str(target),
        requested=ref,
        version=str(manifest.get("version") or ""),
        fingerprint=fp,
    )
    _update_meta(entry, state)

    return InstallResult(
        ok=True,
        target=target,
        name=plugin_name,
        version=entry.version or None,
        manifest=manifest,
        state=state,
    )


def remove(name: str) -> bool:
    """Remove a plugin from the cache and meta.json.

    Returns:
        True if removed, False if not found.
    """
    with _FileLock(name):
        target = get_cache_dir() / name
        removed = False
        if target.exists():
            _rmtree_force(target)
            removed = True

        store = _load_meta()
        if name in store:
            del store[name]
            _save_meta(store)
            removed = True

        return removed


def update(name: str) -> InstallResult:
    """Update a cached plugin via git pull (or reinstall for file sources).

    Returns:
        InstallResult describing the outcome.
    """
    entry = _meta_entry_for(name)
    if entry is None:
        return InstallResult(ok=False, error=f"Plugin not installed: {name}")

    # Re-run install with the original spec and force=True to refresh
    return install(entry.spec, name=name, force=True)
