"""Language Server Protocol integration — Tier 2 #5.

Lets agents use semantic code navigation (goto-definition, find-references,
hover, diagnostics) instead of relying on grep alone. Compared with text
search, LSP is compiler-accurate: it respects scope, imports, renames,
overloads, and type inference.

Package layout:
- ``protocol``  — minimal JSON-RPC message framing + LSP types
- ``client``    — stdio LSP client (one subprocess per language server)
- ``manager``   — per-language singleton with health state and lifecycle

Public helpers are re-exported here for import convenience.
"""

from aru.lsp.client import LspClient, LspRequestError
from aru.lsp.manager import LspHealthState, LspManager, get_lsp_manager
from aru.lsp.protocol import Location, Position, Range

__all__ = [
    "LspClient",
    "LspHealthState",
    "LspManager",
    "LspRequestError",
    "Location",
    "Position",
    "Range",
    "get_lsp_manager",
]
