"""
rei-verify — 共通 infrastructure for refutation-machine and framing-drift-detector.

反証機械 (refutation machine) の 心臓部を 支える 4 primitives:
  1. Verdict (4-value enum: CONFIRMED / REFUTED / HOLDING / INCOMPLETE_FRAME)
  2. IncompleteMarker (「試されずに 残ったもの」 の 型化)
  3. AuditChain (hash-chained append-only JSONL)
  4. VerifiedExecution (pre-check + action + post-check + audit atomically)

Design principle: 沈黙を 成功と 偽装しない。
  CONFIRMED 以外の 全 verdict に IncompleteMarker が 必須。

See DESIGN.md for full rationale.
"""
from __future__ import annotations

from .core import (
    Verdict,
    IncompleteMarker,
    PostCheckResult,
    VerdictWithMarkers,
    VerifiedExecution,
)
from .audit import AuditChain, ChainVerification

__version__ = "0.1.0-alpha"
__author__ = "Nobuki Fujimoto"

__all__ = [
    "Verdict",
    "IncompleteMarker",
    "PostCheckResult",
    "VerdictWithMarkers",
    "VerifiedExecution",
    "AuditChain",
    "ChainVerification",
    "__version__",
]

# MCP tool functions + refute helpers are exported lazily (avoid mcp/subprocess imports
# at package load — keeps core importable in constrained environments)
def __getattr__(name: str):
    if name in (
        "tool_create_audit_chain",
        "tool_append_audit_entry",
        "tool_verify_audit_chain",
        "tool_record_verdict",
        "tool_refute_lean",
    ):
        from .mcp import (
            tool_create_audit_chain,
            tool_append_audit_entry,
            tool_verify_audit_chain,
            tool_record_verdict,
            tool_refute_lean,
        )
        return {
            "tool_create_audit_chain": tool_create_audit_chain,
            "tool_append_audit_entry": tool_append_audit_entry,
            "tool_verify_audit_chain": tool_verify_audit_chain,
            "tool_record_verdict": tool_record_verdict,
            "tool_refute_lean": tool_refute_lean,
        }[name]
    if name in (
        "refute_lean_source",
        "parse_lean_axioms",
        "classify_axioms",
        "DEFAULT_ALLOWED_AXIOMS",
        "AxiomParseResult",
    ):
        from . import refute as _refute
        return getattr(_refute, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
