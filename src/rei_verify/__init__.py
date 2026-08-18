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
        "tool_search_counterexample_explicit",
        "tool_assert_breakpoints_explicit",
        "tool_hold_verdict",
    ):
        from .mcp import (
            tool_create_audit_chain,
            tool_append_audit_entry,
            tool_verify_audit_chain,
            tool_record_verdict,
            tool_refute_lean,
            tool_search_counterexample_explicit,
            tool_assert_breakpoints_explicit,
            tool_hold_verdict,
        )
        return {
            "tool_create_audit_chain": tool_create_audit_chain,
            "tool_append_audit_entry": tool_append_audit_entry,
            "tool_verify_audit_chain": tool_verify_audit_chain,
            "tool_record_verdict": tool_record_verdict,
            "tool_refute_lean": tool_refute_lean,
            "tool_search_counterexample_explicit": tool_search_counterexample_explicit,
            "tool_assert_breakpoints_explicit": tool_assert_breakpoints_explicit,
            "tool_hold_verdict": tool_hold_verdict,
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
    if name in (
        "search_counterexample",
        "compile_predicate_expression",
        "RestrictedExpressionError",
    ):
        from . import search as _search
        return getattr(_search, name)
    if name in (
        "Breakpoint",
        "assert_breakpoints",
    ):
        from . import breakpoint as _bp
        return getattr(_bp, name)
    if name == "hold_verdict":
        from . import hold as _hold
        return _hold.hold_verdict
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
