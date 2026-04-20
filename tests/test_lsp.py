"""Stage 5 Tier 2: LSP integration tests.

We don't depend on a real language server — tests use an in-process fake
client that records requests and feeds scripted responses. The tools,
manager, and protocol code are exercised end-to-end; the only mocked
boundary is ``LspManager.get_client_for`` returning the fake.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aru.lsp.manager import (
    LspManager,
    LspServerHealth,
    install_lsp_from_config,
    set_lsp_manager,
)
from aru.lsp.protocol import path_to_uri, uri_to_path
from aru.tools.lsp import (
    lsp_definition,
    lsp_diagnostics,
    lsp_hover,
    lsp_references,
)


# ── Protocol helpers ──────────────────────────────────────────────────

def test_path_to_uri_roundtrip(tmp_path):
    f = tmp_path / "sub" / "file.py"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    uri = path_to_uri(str(f))
    assert uri.startswith("file://")
    back = uri_to_path(uri)
    assert Path(back).resolve() == f.resolve()


# ── Manager ───────────────────────────────────────────────────────────

def test_language_for_file_matches_config():
    mgr = LspManager(config_lsp={"python": {"command": "pylsp"}})
    assert mgr.language_for_file("/x/y.py") == "python"
    assert mgr.language_for_file("/x/y.rs") is None  # not configured
    assert mgr.language_for_file("/x/y.txt") is None


def test_install_lsp_from_config_none_with_empty():
    set_lsp_manager(None)
    mgr = install_lsp_from_config(None, root="/tmp")
    assert mgr is None


def test_install_lsp_from_config_creates_manager():
    mgr = install_lsp_from_config({"python": {"command": "pylsp"}}, root="/tmp")
    assert mgr is not None
    assert mgr.language_for_file("/tmp/a.py") == "python"


# ── Tool fakes ────────────────────────────────────────────────────────

class FakeClient:
    """Stand-in for ``LspClient`` — no subprocess, scripted responses."""

    def __init__(self):
        self.requests: list[tuple[str, dict]] = []
        self.scripted: dict[str, object] = {}
        self.opened_docs: set[str] = set()
        self.diagnostics: dict[str, list] = {}

    async def ensure_open(self, uri: str, language_id: str, text: str) -> None:
        self.opened_docs.add(uri)

    async def request(self, method, params):
        self.requests.append((method, params))
        return self.scripted.get(method)

    def diagnostics_for(self, uri: str) -> list[dict]:
        return list(self.diagnostics.get(uri, []))


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    """Install a manager whose get_client_for returns a FakeClient."""
    f = tmp_path / "src.py"
    f.write_text("def foo():\n    return 1\n")
    mgr = LspManager(config_lsp={"python": {"command": "pylsp"}}, root=str(tmp_path))
    fake = FakeClient()
    mgr.get_client_for = AsyncMock(return_value=fake)  # type: ignore[assignment]
    set_lsp_manager(mgr)
    return mgr, fake, str(f)


@pytest.mark.asyncio
async def test_lsp_definition_formats_single_location(fake_env, tmp_path):
    mgr, fake, file_path = fake_env
    target = tmp_path / "target.py"
    target.write_text("x\n")
    fake.scripted["textDocument/definition"] = {
        "uri": path_to_uri(str(target)),
        "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 3},
        },
    }
    out = await lsp_definition(file_path, 0, 4)
    assert "target.py:1:1" in out
    # Document was opened before request
    assert path_to_uri(file_path) in fake.opened_docs
    # Request carries the position
    method, params = fake.requests[-1]
    assert method == "textDocument/definition"
    assert params["position"] == {"line": 0, "character": 4}


@pytest.mark.asyncio
async def test_lsp_definition_no_result(fake_env):
    _mgr, fake, file_path = fake_env
    fake.scripted["textDocument/definition"] = None
    out = await lsp_definition(file_path, 0, 4)
    assert out == "No result."


@pytest.mark.asyncio
async def test_lsp_references_lists_multiple_locations(fake_env, tmp_path):
    _mgr, fake, file_path = fake_env
    a = tmp_path / "a.py"; a.write_text("x\n")
    b = tmp_path / "b.py"; b.write_text("y\n")
    fake.scripted["textDocument/references"] = [
        {"uri": path_to_uri(str(a)), "range": {
            "start": {"line": 0, "character": 0},
            "end": {"line": 0, "character": 1}}},
        {"uri": path_to_uri(str(b)), "range": {
            "start": {"line": 2, "character": 4},
            "end": {"line": 2, "character": 7}}},
    ]
    out = await lsp_references(file_path, 0, 4)
    assert "a.py:1:1" in out
    assert "b.py:3:5" in out


@pytest.mark.asyncio
async def test_lsp_references_include_declaration_param_propagates(fake_env):
    _mgr, fake, file_path = fake_env
    fake.scripted["textDocument/references"] = []
    await lsp_references(file_path, 0, 0, include_declaration=False)
    method, params = fake.requests[-1]
    assert method == "textDocument/references"
    assert params["context"]["includeDeclaration"] is False


@pytest.mark.asyncio
async def test_lsp_hover_extracts_markdown_value(fake_env):
    _mgr, fake, file_path = fake_env
    fake.scripted["textDocument/hover"] = {
        "contents": {"kind": "markdown", "value": "foo(): return 1"},
    }
    out = await lsp_hover(file_path, 0, 4)
    assert "foo()" in out


@pytest.mark.asyncio
async def test_lsp_hover_handles_list_contents(fake_env):
    _mgr, fake, file_path = fake_env
    fake.scripted["textDocument/hover"] = {
        "contents": [
            {"kind": "plaintext", "value": "first"},
            "second",
        ],
    }
    out = await lsp_hover(file_path, 0, 0)
    assert "first" in out and "second" in out


@pytest.mark.asyncio
async def test_lsp_hover_empty(fake_env):
    _mgr, fake, file_path = fake_env
    fake.scripted["textDocument/hover"] = None
    out = await lsp_hover(file_path, 0, 0)
    assert "No hover" in out


@pytest.mark.asyncio
async def test_lsp_diagnostics_rendered(fake_env):
    _mgr, fake, file_path = fake_env
    fake.diagnostics[path_to_uri(file_path)] = [
        {
            "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 7}},
            "severity": 1,
            "source": "pylsp",
            "message": "undefined name 'foo'",
        },
        {
            "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 3}},
            "severity": 2,
            "message": "unused variable",
        },
    ]
    out = await lsp_diagnostics(file_path)
    assert "src.py:1:5" in out
    assert "[error:pylsp]" in out
    assert "unused variable" in out
    assert "[warning]" in out


@pytest.mark.asyncio
async def test_lsp_tools_report_missing_manager():
    set_lsp_manager(None)
    out = await lsp_definition("/tmp/x.py", 0, 0)
    assert "LSP not configured" in out


@pytest.mark.asyncio
async def test_lsp_tools_report_unsupported_language(tmp_path):
    # Manager configured only for python; .rs file should fall through.
    mgr = LspManager(config_lsp={"python": {"command": "pylsp"}}, root=str(tmp_path))
    set_lsp_manager(mgr)
    out = await lsp_definition(str(tmp_path / "some.rs"), 0, 0)
    assert "not configured" in out.lower() or "not available" in out.lower()


@pytest.mark.asyncio
async def test_lsp_failed_startup_is_surfaced(tmp_path):
    mgr = LspManager(config_lsp={"python": {"command": "pylsp"}}, root=str(tmp_path))
    mgr.health["python"] = LspServerHealth(
        name="python", state="failed", last_error="binary missing"
    )
    # Simulate get_client_for returning None without trying to spawn
    mgr.get_client_for = AsyncMock(return_value=None)  # type: ignore[assignment]
    set_lsp_manager(mgr)
    f = tmp_path / "x.py"; f.write_text("x\n")
    out = await lsp_definition(str(f), 0, 0)
    assert "unavailable" in out.lower()
    assert "binary missing" in out
