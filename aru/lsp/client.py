"""Minimal async LSP stdio client.

Spawns a language-server subprocess (``pylsp``, ``typescript-language-server``,
etc.), performs the LSP initialize handshake, then offers ``request`` /
``notify`` primitives used by the four agent-facing tools.

Per-document synchronization (didOpen / didChange) is handled by the
manager when a tool touches a file the server hasn't seen yet.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from aru.lsp.protocol import encode_message, read_message

logger = logging.getLogger("aru.lsp")


class LspRequestError(RuntimeError):
    """Server returned an error response or the connection died."""


@dataclass
class LspClient:
    """One spawned language server. Thread-confined to the event loop."""

    command: list[str]
    root_uri: str
    cwd: str = field(default_factory=os.getcwd)
    env: dict[str, str] | None = None

    process: Any = None                                 # asyncio.subprocess.Process
    _writer: Any = None
    _reader: Any = None
    _next_id: int = 1
    _pending: dict[int, asyncio.Future] = field(default_factory=dict)
    _reader_task: Any = None
    _open_docs: set[str] = field(default_factory=set)
    _diagnostics: dict[str, list[dict]] = field(default_factory=dict)
    _initialized: bool = False

    async def start(self) -> None:
        """Spawn the process and perform the LSP initialize handshake."""
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env={**os.environ, **(self.env or {})},
        )
        self._writer = self.process.stdin
        self._reader = self.process.stdout
        self._reader_task = asyncio.create_task(self._read_loop())

        await self.request("initialize", {
            "processId": os.getpid(),
            "rootUri": self.root_uri,
            "capabilities": {
                "textDocument": {
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                    "hover": {"dynamicRegistration": False},
                    "publishDiagnostics": {"relatedInformation": False},
                    "synchronization": {"didSave": False},
                },
            },
        })
        self.notify("initialized", {})
        self._initialized = True

    async def shutdown(self) -> None:
        """Ask the server to shut down, then close the process."""
        if self._initialized and self._writer is not None:
            try:
                await asyncio.wait_for(self.request("shutdown", None), timeout=2.0)
                self.notify("exit", None)
            except (asyncio.TimeoutError, LspRequestError, ConnectionError):
                pass
        if self._reader_task is not None:
            self._reader_task.cancel()
        if self.process is not None:
            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self.process.kill()
                except ProcessLookupError:
                    pass

    # -- Core JSON-RPC -----------------------------------------------

    async def request(self, method: str, params: Any) -> Any:
        """Send a request and await its response. Raises LspRequestError on server error."""
        if self._writer is None:
            raise LspRequestError("LSP client not started")
        req_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._writer.write(encode_message(payload))
        try:
            await self._writer.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            self._pending.pop(req_id, None)
            raise LspRequestError(f"pipe closed: {exc}") from exc
        try:
            return await asyncio.wait_for(fut, timeout=30)
        except asyncio.TimeoutError as exc:
            self._pending.pop(req_id, None)
            raise LspRequestError(f"request {method} timed out") from exc

    def notify(self, method: str, params: Any) -> None:
        """Fire-and-forget notification (no id, no response)."""
        if self._writer is None:
            return
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._writer.write(encode_message(payload))
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def _read_loop(self) -> None:
        """Dispatch inbound messages to pending requests or diagnostics cache."""
        while True:
            msg = await read_message(self._reader)
            if msg is None:
                self._fail_pending("connection closed")
                return
            if "id" in msg and ("result" in msg or "error" in msg):
                fut = self._pending.pop(int(msg["id"]), None)
                if fut is None:
                    continue
                if "error" in msg:
                    fut.set_exception(LspRequestError(
                        msg["error"].get("message", "unknown LSP error")
                    ))
                else:
                    fut.set_result(msg.get("result"))
            elif msg.get("method") == "textDocument/publishDiagnostics":
                params = msg.get("params") or {}
                uri = params.get("uri")
                if uri:
                    self._diagnostics[uri] = params.get("diagnostics", []) or []

    def _fail_pending(self, reason: str) -> None:
        exc = LspRequestError(reason)
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()

    # -- Document sync + convenience -------------------------------

    async def ensure_open(self, uri: str, language_id: str, text: str) -> None:
        if uri in self._open_docs:
            return
        self.notify("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": 1,
                "text": text,
            },
        })
        self._open_docs.add(uri)

    def notify_change(self, uri: str, text: str) -> None:
        self.notify("textDocument/didChange", {
            "textDocument": {"uri": uri, "version": 2},
            "contentChanges": [{"text": text}],
        })

    def diagnostics_for(self, uri: str) -> list[dict]:
        return list(self._diagnostics.get(uri, []))
