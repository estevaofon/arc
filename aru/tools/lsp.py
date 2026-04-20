"""Agent-facing LSP tools (Tier 2 #5).

Four semantic operations. Each returns a short, agent-readable string so
the LLM can integrate the result directly into reasoning. Failures (LSP
unavailable, server crashed, file unsupported) surface as explanatory
strings rather than raises — the model can fall back to grep-based tools.
"""

from __future__ import annotations

import os

from aru.lsp.client import LspRequestError
from aru.lsp.manager import get_lsp_manager
from aru.lsp.protocol import Location, Position, path_to_uri


async def _ensure_doc(client, path: str) -> tuple[str, str]:
    uri = path_to_uri(path)
    ext = os.path.splitext(path)[1].lower()
    lang_hint = {
        ".py": "python", ".pyi": "python",
        ".ts": "typescript", ".tsx": "typescriptreact",
        ".js": "javascript", ".jsx": "javascriptreact",
        ".rs": "rust", ".go": "go",
    }.get(ext, "plaintext")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        raise LspRequestError(f"cannot read {path}: {exc}") from exc
    await client.ensure_open(uri, lang_hint, text)
    return uri, text


async def _call(path: str, method: str, position: Position,
                extra_params: dict | None = None) -> str | list[Location] | dict | None:
    mgr = get_lsp_manager()
    if mgr is None:
        return "[LSP not configured. Add an \"lsp\" section to aru.json.]"
    client = await mgr.get_client_for(path)
    if client is None:
        health = mgr.health.get(mgr.language_for_file(path) or "")
        if health and health.state == "failed":
            return f"[LSP server unavailable: {health.last_error}]"
        return f"[LSP not configured for {path}]"
    try:
        uri, _ = await _ensure_doc(client, path)
    except LspRequestError as exc:
        return f"[LSP error: {exc}]"
    params = {
        "textDocument": {"uri": uri},
        "position": position.to_wire(),
    }
    if extra_params:
        params.update(extra_params)
    try:
        return await client.request(method, params)
    except LspRequestError as exc:
        return f"[LSP error: {exc}]"


def _format_locations(raw) -> str:
    if raw is None:
        return "No result."
    if isinstance(raw, str):
        return raw  # error message
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, list) and not raw:
        return "No result."
    lines: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            loc = Location.from_wire(item)
        except KeyError:
            continue
        lines.append(loc.as_human())
    return "\n".join(lines) if lines else "No result."


# ── Tools ───────────────────────────────────────────────────────────

async def lsp_definition(file_path: str, line: int, column: int) -> str:
    """Locate the definition of the symbol at the given file:line:column.

    Line and column are 0-indexed (LSP convention). Returns a ``path:line:col``
    string (1-indexed for human readability) or an explanatory error. Use this
    instead of ``grep_search`` when you need to jump to where a name is defined
    in a codebase that may have multiple symbols with the same string.
    """
    raw = await _call(file_path, "textDocument/definition", Position(line, column))
    return _format_locations(raw)


async def lsp_references(file_path: str, line: int, column: int,
                         include_declaration: bool = True) -> str:
    """Find every reference to the symbol at file:line:column.

    Returns one ``path:line:col`` per line. ``include_declaration=False`` skips
    the declaration site itself (useful when you want only call sites).
    """
    raw = await _call(
        file_path, "textDocument/references", Position(line, column),
        extra_params={"context": {"includeDeclaration": bool(include_declaration)}},
    )
    return _format_locations(raw)


async def lsp_hover(file_path: str, line: int, column: int) -> str:
    """Return the type/docstring for the symbol at file:line:column.

    Condenses the LSP hover ``MarkupContent`` into plain text.
    """
    raw = await _call(file_path, "textDocument/hover", Position(line, column))
    if isinstance(raw, str):
        return raw
    if not raw or not isinstance(raw, dict):
        return "No hover info."
    contents = raw.get("contents")
    if contents is None:
        return "No hover info."
    if isinstance(contents, dict):
        return str(contents.get("value") or "").strip() or "No hover info."
    if isinstance(contents, list):
        pieces: list[str] = []
        for c in contents:
            if isinstance(c, dict):
                pieces.append(str(c.get("value") or ""))
            else:
                pieces.append(str(c))
        return "\n".join(p for p in pieces if p).strip() or "No hover info."
    return str(contents).strip()


async def lsp_diagnostics(file_path: str) -> str:
    """Current errors/warnings for *file_path*, as reported by the LSP server.

    Relies on the server's ``textDocument/publishDiagnostics`` push — results
    reflect whatever the server has sent so far. The client auto-opens the
    document on first access, so most servers populate diagnostics soon after.
    """
    mgr = get_lsp_manager()
    if mgr is None:
        return "[LSP not configured.]"
    client = await mgr.get_client_for(file_path)
    if client is None:
        return f"[LSP not available for {file_path}]"
    try:
        await _ensure_doc(client, file_path)
    except LspRequestError as exc:
        return f"[LSP error: {exc}]"
    uri = path_to_uri(file_path)
    diags = client.diagnostics_for(uri)
    if not diags:
        return "No diagnostics."
    lines: list[str] = []
    sev_names = {1: "error", 2: "warning", 3: "info", 4: "hint"}
    for d in diags:
        try:
            rng = d.get("range", {}).get("start", {})
            line = int(rng.get("line", 0)) + 1
            col = int(rng.get("character", 0)) + 1
            severity = sev_names.get(int(d.get("severity", 1)), "error")
            source = d.get("source", "")
            msg = d.get("message", "")
            prefix = f"{file_path}:{line}:{col}"
            tag = f"[{severity}]"
            if source:
                tag = f"[{severity}:{source}]"
            lines.append(f"{prefix} {tag} {msg}")
        except Exception:  # pragma: no cover — defensive
            continue
    return "\n".join(lines) if lines else "No diagnostics."
