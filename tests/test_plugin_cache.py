"""Tests for the plugin caching and installation system (aru/plugin_cache.py)."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from aru import plugin_cache as pc


@pytest.fixture
def isolated_paths(tmp_path, monkeypatch):
    """Redirect plugin cache, meta, and locks to a temporary directory."""
    aru_home = tmp_path / "aru_home"
    monkeypatch.setattr(pc, "ARU_HOME", aru_home)
    monkeypatch.setattr(pc, "PLUGINS_ROOT", aru_home / "plugins")
    monkeypatch.setattr(pc, "CACHE_DIR", aru_home / "plugins" / "cache" / "packages")
    monkeypatch.setattr(pc, "META_PATH", aru_home / "plugins" / "meta.json")
    monkeypatch.setattr(pc, "LOCK_DIR", aru_home / "plugins" / "locks")
    return aru_home


def _make_plugin_dir(root: Path, manifest: dict | None = None) -> Path:
    """Create a fake plugin on disk with optional manifest + sample resources."""
    root.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (root / "aru-plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "skills").mkdir(exist_ok=True)
    (root / "skills" / "sample").mkdir(exist_ok=True)
    (root / "skills" / "sample" / "SKILL.md").write_text(
        "---\nname: sample\ndescription: test skill\n---\nBody\n", encoding="utf-8"
    )
    (root / "agents").mkdir(exist_ok=True)
    (root / "agents" / "hero.md").write_text(
        "---\nname: hero\n---\nHero agent\n", encoding="utf-8"
    )
    (root / "tools").mkdir(exist_ok=True)
    (root / "tools" / "demo.py").write_text(
        "def demo() -> str:\n    return 'hi'\n", encoding="utf-8"
    )
    (root / "plugins").mkdir(exist_ok=True)
    return root


# ────────────────────────── parse_spec ──────────────────────────


class TestParseSpec:
    def test_github_shorthand(self):
        source, url, ref = pc.parse_spec("github:obra/superpowers")
        assert source == "git"
        assert url == "https://github.com/obra/superpowers.git"
        assert ref is None

    def test_github_shorthand_with_ref(self):
        source, url, ref = pc.parse_spec("github:obra/superpowers@v1.2.3")
        assert source == "git"
        assert url == "https://github.com/obra/superpowers.git"
        assert ref == "v1.2.3"

    def test_git_plus_url(self):
        source, url, ref = pc.parse_spec("git+https://github.com/foo/bar.git")
        assert source == "git"
        assert url == "https://github.com/foo/bar.git"
        assert ref is None

    def test_git_plus_url_with_ref(self):
        source, url, ref = pc.parse_spec("git+https://github.com/foo/bar.git@main")
        assert source == "git"
        assert url == "https://github.com/foo/bar.git"
        assert ref == "main"

    def test_file_url(self, tmp_path):
        path = tmp_path / "myplugin"
        path.mkdir()
        source, resolved, ref = pc.parse_spec(f"file://{path}")
        assert source == "file"
        assert Path(resolved) == path.resolve()
        assert ref is None

    def test_local_absolute_path(self, tmp_path):
        path = tmp_path / "local_plugin"
        path.mkdir()
        source, resolved, ref = pc.parse_spec(str(path))
        assert source == "file"
        assert Path(resolved) == path.resolve()

    def test_unknown_spec_raises(self):
        with pytest.raises(ValueError):
            pc.parse_spec("not-a-valid-spec-no-namespace")


class TestInferName:
    def test_git_url_with_git_suffix(self):
        assert pc.infer_name(
            "git+https://github.com/user/my-plugin.git",
            "git",
            "https://github.com/user/my-plugin.git",
        ) == "my-plugin"

    def test_file_path(self):
        assert pc.infer_name(
            "file:///abs/path/my-plugin",
            "file",
            "/abs/path/my-plugin",
        ) == "my-plugin"


# ────────────────────────── compatibility ──────────────────────────


class TestCompatibility:
    def test_no_engines_field_passes(self):
        pc.check_compatibility({}, "0.27.0")  # no raise

    def test_gte_satisfied(self):
        pc.check_compatibility({"engines": {"aru": ">=0.26.0"}}, "0.27.0")

    def test_gte_unsatisfied_raises(self):
        with pytest.raises(ValueError, match="Plugin requires"):
            pc.check_compatibility({"engines": {"aru": ">=1.0.0"}}, "0.27.0")

    def test_caret_satisfied(self):
        pc.check_compatibility({"engines": {"aru": "^0.27.0"}}, "0.27.5")

    def test_caret_major_bump_fails(self):
        with pytest.raises(ValueError):
            pc.check_compatibility({"engines": {"aru": "^1.0.0"}}, "2.0.0")

    def test_wildcard_passes(self):
        pc.check_compatibility({"engines": {"aru": "*"}}, "999.0.0")


# ────────────────────────── fingerprint ──────────────────────────


class TestFingerprint:
    def test_stable_for_same_content(self, tmp_path):
        plugin_dir = tmp_path / "plugin"
        _make_plugin_dir(plugin_dir, {"name": "x", "version": "1.0"})

        fp1 = pc.fingerprint(plugin_dir)
        fp2 = pc.fingerprint(plugin_dir)
        assert fp1 == fp2
        assert fp1.startswith("sha256:")

    def test_changes_when_file_modified(self, tmp_path):
        plugin_dir = tmp_path / "plugin"
        _make_plugin_dir(plugin_dir, {"name": "x", "version": "1.0"})

        fp1 = pc.fingerprint(plugin_dir)
        time.sleep(1.1)  # ensure mtime changes at 1s resolution
        (plugin_dir / "skills" / "sample" / "SKILL.md").write_text(
            "new content", encoding="utf-8"
        )
        fp2 = pc.fingerprint(plugin_dir)
        assert fp1 != fp2

    def test_ignores_git_directory(self, tmp_path):
        plugin_dir = tmp_path / "plugin"
        _make_plugin_dir(plugin_dir)
        git_dir = plugin_dir / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

        fp_with_git = pc.fingerprint(plugin_dir)
        # Modifying .git should not change fingerprint
        (git_dir / "HEAD").write_text("ref: refs/heads/dev\n", encoding="utf-8")
        fp_after = pc.fingerprint(plugin_dir)
        assert fp_with_git == fp_after


# ────────────────────────── manifest I/O ──────────────────────────


class TestManifest:
    def test_read_valid_manifest(self, tmp_path):
        p = tmp_path / "plugin"
        p.mkdir()
        (p / "aru-plugin.json").write_text(
            json.dumps({"name": "demo", "version": "1.0.0"}), encoding="utf-8"
        )
        manifest = pc.read_manifest(p)
        assert manifest["name"] == "demo"

    def test_missing_manifest_returns_empty(self, tmp_path):
        p = tmp_path / "no_manifest"
        p.mkdir()
        assert pc.read_manifest(p) == {}

    def test_invalid_json_returns_empty(self, tmp_path):
        p = tmp_path / "bad"
        p.mkdir()
        (p / "aru-plugin.json").write_text("{not json", encoding="utf-8")
        assert pc.read_manifest(p) == {}


# ────────────────────────── install / remove / update ──────────────────────────


class TestInstallFromFile:
    def test_install_local_plugin(self, tmp_path, isolated_paths):
        src = tmp_path / "source_plugin"
        _make_plugin_dir(
            src, {"name": "source_plugin", "version": "0.1.0", "engines": {"aru": ">=0.0.0"}}
        )

        result = pc.install(f"file://{src}")
        assert result.ok, f"install failed: {result.error}"
        assert result.name == "source_plugin"
        assert result.version == "0.1.0"
        assert result.target is not None
        assert (result.target / "skills" / "sample" / "SKILL.md").is_file()
        # Meta was written
        entries = pc.list_installed()
        assert len(entries) == 1
        assert entries[0].id == "source_plugin"

    def test_install_fails_on_incompatible_engine(self, tmp_path, isolated_paths):
        src = tmp_path / "incompat"
        _make_plugin_dir(src, {"name": "incompat", "version": "1.0", "engines": {"aru": ">=999.0.0"}})
        result = pc.install(f"file://{src}")
        assert not result.ok
        assert "Plugin requires" in (result.error or "")
        # Cache cleaned up
        assert not (pc.CACHE_DIR / "incompat").exists()

    def test_install_provides_counts(self, tmp_path, isolated_paths):
        src = tmp_path / "counts"
        _make_plugin_dir(src, {"name": "counts", "version": "1.0"})
        result = pc.install(f"file://{src}")
        assert result.ok
        provides = result.provides
        assert provides.get("skills") == 1
        assert provides.get("agents") == 1
        assert provides.get("tools") == 1

    def test_reinstall_updates_state(self, tmp_path, isolated_paths):
        src = tmp_path / "reinstall"
        _make_plugin_dir(src, {"name": "reinstall", "version": "1.0"})

        first = pc.install(f"file://{src}")
        assert first.ok
        assert first.state == "first"

        # Same content -> "same"
        same = pc.install(f"file://{src}", force=True)
        assert same.ok
        assert same.state == "same"

        # Change content -> "updated"
        time.sleep(1.1)
        (src / "skills" / "sample" / "SKILL.md").write_text("changed\n", encoding="utf-8")
        updated = pc.install(f"file://{src}", force=True)
        assert updated.ok
        assert updated.state == "updated"

    def test_remove_cleans_cache_and_meta(self, tmp_path, isolated_paths):
        src = tmp_path / "to_remove"
        _make_plugin_dir(src, {"name": "to_remove", "version": "1.0"})
        pc.install(f"file://{src}")
        assert (pc.CACHE_DIR / "to_remove").is_dir()

        assert pc.remove("to_remove")
        assert not (pc.CACHE_DIR / "to_remove").exists()
        assert pc.list_installed() == []

    def test_remove_nonexistent_returns_false(self, isolated_paths):
        assert pc.remove("does-not-exist") is False

    def test_update_reinstalls(self, tmp_path, isolated_paths):
        src = tmp_path / "upd"
        _make_plugin_dir(src, {"name": "upd", "version": "1.0"})
        pc.install(f"file://{src}")

        # Modify source, then update
        time.sleep(1.1)
        (src / "skills" / "sample" / "SKILL.md").write_text("v2\n", encoding="utf-8")
        result = pc.update("upd")
        assert result.ok


# ────────────────────────── discovery integration ──────────────────────────


class TestDiscoveryIntegration:
    def test_get_cached_plugin_roots_empty(self, isolated_paths):
        assert pc.get_cached_plugin_roots() == []

    def test_get_cached_plugin_roots_lists_installed(self, tmp_path, isolated_paths):
        src = tmp_path / "discoverable"
        _make_plugin_dir(src, {"name": "discoverable", "version": "1.0"})
        pc.install(f"file://{src}")

        roots = pc.get_cached_plugin_roots()
        assert len(roots) == 1
        assert roots[0].name == "discoverable"

    def test_cached_skills_flow_into_config_discovery(self, tmp_path, isolated_paths, monkeypatch):
        # Install a plugin with a skill
        src = tmp_path / "skill_bearer"
        _make_plugin_dir(src, {"name": "skill_bearer", "version": "1.0"})
        pc.install(f"file://{src}")

        # Stage a project root so config.load_config runs cleanly
        project_root = tmp_path / "proj"
        project_root.mkdir()
        monkeypatch.chdir(project_root)

        from aru.config import load_config
        cfg = load_config(str(project_root))
        assert "sample" in cfg.skills
        assert cfg.skills["sample"].description == "test skill"


# ────────────────────────── lock behavior ──────────────────────────


class TestFileLock:
    def test_lock_creates_and_releases(self, isolated_paths):
        lock = pc._FileLock("test_lock")
        lock.acquire()
        assert lock.path.exists()
        lock.release()
        assert not lock.path.exists()

    def test_second_acquire_blocks_until_release(self, isolated_paths):
        lock1 = pc._FileLock("contended")
        lock1.acquire()
        lock2 = pc._FileLock("contended")
        with pytest.raises(TimeoutError):
            lock2.acquire(timeout=0.5)
        lock1.release()
        lock2.acquire(timeout=1.0)  # now succeeds
        lock2.release()

    def test_stale_lock_is_reclaimed(self, isolated_paths, monkeypatch):
        lock = pc._FileLock("stale")
        lock.acquire()
        # Backdate the lock beyond TTL
        old = time.time() - (pc.LOCK_TTL_SECONDS + 60)
        import os
        os.utime(lock.path, (old, old))

        lock2 = pc._FileLock("stale")
        lock2.acquire(timeout=2.0)
        assert lock2.path.exists()
        lock2.release()
