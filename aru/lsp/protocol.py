"""Minimal LSP types + JSON-RPC framing helpers.

We deliberately avoid pulling in a fat LSP library — only a handful of
messages matter for the four tools we expose (definition, references,
hover, diagnostics). The dataclasses mirror the wire format closely so
responses can be constructed from ``dict`` without bespoke decoding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


# ── Basic positional types ───────────────────────────────────────────

@dataclass
class Position:
    """0-indexed position (line, character) as LSP uses them."""
    line: int
    character: int

    def to_wire(self) -> dict:
        return {"line": self.line, "character": self.character}

    @classmethod
    def from_wire(cls, data: dict) -> "Position":
        return cls(line=int(data["line"]), character=int(data["character"]))


@dataclass
class Range:
    start: Position
    end: Position

    @classmethod
    def from_wire(cls, data: dict) -> "Range":
        return cls(
            start=Position.from_wire(data["start"]),
            end=Position.from_wire(data["end"]),
        )


@dataclass
class Location:
    uri: str
    range: Range

    @classmethod
    def from_wire(cls, data: dict) -> "Location":
        return cls(uri=data["uri"], range=Range.from_wire(data["range"]))

    def as_human(self) -> str:
        """Render as ``path:line:col`` for agent-readable output."""
        path = uri_to_path(self.uri)
        line = self.range.start.line + 1  # LSP is 0-indexed; users expect 1-indexed
        col = self.range.start.character + 1
        return f"{path}:{line}:{col}"


# ── URI helpers (Windows-safe) ───────────────────────────────────────

def path_to_uri(path: str) -> str:
    """Absolute path -> ``file:///…`` URI. Handles Windows drive letters."""
    p = Path(path).resolve()
    posix = p.as_posix()
    # as_posix returns ``C:/foo`` on Windows; LSP expects ``/C:/foo``.
    if len(posix) > 1 and posix[1] == ":":
        posix = "/" + posix
    return "file://" + posix


def uri_to_path(uri: str) -> str:
    """``file:///…`` URI -> absolute path. Inverse of :func:`path_to_uri`."""
    if uri.startswith("file:///"):
        body = uri[len("file:///"):]
        # Windows: drive-letter form was normalised with a leading ``/``;
        # strip it so we get ``C:/foo`` back.
        if len(body) > 1 and body[1] == ":":
            return str(Path(body).resolve())
        return str(Path("/" + body).resolve())
    if uri.startswith("file://"):
        return str(Path(uri[len("file://"):]).resolve())
    return uri


# ── JSON-RPC framing ─────────────────────────────────────────────────

def encode_message(payload: dict) -> bytes:
    """Frame a JSON payload with the LSP ``Content-Length`` header."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    return header + body


async def read_message(reader) -> dict | None:
    """Read a single framed message from *reader*. Returns None on EOF."""
    content_length = 0
    # Parse headers
    while True:
        line = await reader.readline()
        if not line:
            return None  # EOF
        line = line.rstrip(b"\r\n")
        if not line:
            break  # blank line ends headers
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            content_length = int(value.strip())
    if content_length <= 0:
        return None
    body = await reader.readexactly(content_length)
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None
