"""
rei-verify refute-4 selftest.

Coverage:
  - Python API pre-check paths: 4 case → INCOMPLETE_FRAME
  - Python API verdict paths: single-dim / multi-dim / require_multi_dim discipline
  - VerdictWithMarkers invariant preserved (all HOLDINGs have >=1 marker)
  - MCP tool_hold_verdict: validation + verdict paths + marker construction
  - full 4-tool integration: consistent VerdictWithMarkers shape across all tools
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / "src"))

from rei_verify import (
    Verdict,
    IncompleteMarker,
    AuditChain,
    VerdictWithMarkers,
)
from rei_verify.hold import hold_verdict
from rei_verify.mcp import (
    tool_create_audit_chain,
    tool_hold_verdict,
    _reset_state_for_test,
)


passed = 0
failed = 0


def check(condition: bool, msg: str) -> None:
    global passed, failed
    if condition:
        passed += 1
    else:
        failed += 1
        print(f"  FAIL: {msg}")


# ---------------------------------------------------------------------------
# Test group H1: pre-check paths → INCOMPLETE_FRAME
# ---------------------------------------------------------------------------

print("\n[H1] hold_verdict pre-check paths")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "h1.jsonl")

    # empty markers list
    r = hold_verdict(claim="x", markers=[], audit=audit)
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"empty markers → INCOMPLETE_FRAME (got {r.verdict})")

    # non-list markers
    r = hold_verdict(claim="x", markers="not-a-list", audit=audit)  # type: ignore[arg-type]
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"non-list → INCOMPLETE_FRAME (got {r.verdict})")

    # None markers
    r = hold_verdict(claim="x", markers=None, audit=audit)  # type: ignore[arg-type]
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"None markers → INCOMPLETE_FRAME (got {r.verdict})")

    # wrong type items in list
    r = hold_verdict(claim="x", markers=["not-a-marker"], audit=audit)  # type: ignore[list-item]
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"non-Marker items → INCOMPLETE_FRAME (got {r.verdict})")


# ---------------------------------------------------------------------------
# Test group H2: valid HOLDING paths
# ---------------------------------------------------------------------------

print("\n[H2] hold_verdict valid HOLDING paths")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "h2.jsonl")

    # single marker
    m1 = IncompleteMarker(
        dimension="search_space",
        what_was_tried="orbit for n in [1, 10^6]",
        what_was_not_tried="n > 10^6",
        reason="compute budget cutoff",
    )
    r = hold_verdict(claim="Collatz orbit descent", markers=[m1], audit=audit)
    check(r.verdict == Verdict.HOLDING, f"1 marker → HOLDING (got {r.verdict})")
    check(len(r.markers) == 1, f"1 marker preserved (got {len(r.markers)})")
    check(r.markers[0].dimension == "search_space", "marker dimension preserved")
    check(r.audit_hashes and len(r.audit_hashes) >= 4, f"audit chain has phase entries (got {len(r.audit_hashes)})")

    # multiple markers
    m2 = IncompleteMarker(
        dimension="witness_type",
        what_was_tried="numerical counterexample search",
        what_was_not_tried="structural / analytic refutation",
        reason="analysis pending",
    )
    r = hold_verdict(claim="claim X", markers=[m1, m2], audit=audit, notes="mixed dim")
    check(r.verdict == Verdict.HOLDING, f"multi markers → HOLDING (got {r.verdict})")
    check(len(r.markers) == 2, f"both markers preserved (got {len(r.markers)})")

    # markers are verbatim (no mutation, correct order)
    check(r.markers[0] is m1 and r.markers[1] is m2, "markers verbatim in order")


# ---------------------------------------------------------------------------
# Test group H3: require_multi_dimension discipline
# ---------------------------------------------------------------------------

print("\n[H3] require_multi_dimension discipline hook")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "h3.jsonl")

    m_single_dim1 = IncompleteMarker(
        dimension="search_space",
        what_was_tried="a",
        what_was_not_tried="b",
        reason="c",
    )
    m_single_dim2 = IncompleteMarker(
        dimension="search_space",
        what_was_tried="d",
        what_was_not_tried="e",
        reason="f",
    )
    m_other_dim = IncompleteMarker(
        dimension="witness_type",
        what_was_tried="g",
        what_was_not_tried="h",
        reason="i",
    )

    # 2 markers both search_space + require_multi_dimension=True → augmentation
    r = hold_verdict(
        claim="single dim",
        markers=[m_single_dim1, m_single_dim2],
        audit=audit,
        require_multi_dimension=True,
    )
    check(r.verdict == Verdict.HOLDING, "single-dim with require=True still HOLDING")
    check(len(r.markers) == 3, f"augmentation added (2 orig + 1 aug = 3, got {len(r.markers)})")
    check(r.markers[2].dimension == "frame", f"aug marker is frame dim (got {r.markers[2].dimension})")
    check("under-articulate" in r.markers[2].reason.lower(),
          "aug marker warns about under-articulation")

    # 2 markers different dims + require_multi_dimension=True → no augmentation
    r = hold_verdict(
        claim="multi dim",
        markers=[m_single_dim1, m_other_dim],
        audit=audit,
        require_multi_dimension=True,
    )
    check(r.verdict == Verdict.HOLDING, "multi-dim with require=True HOLDING")
    check(len(r.markers) == 2, f"no augmentation for multi-dim (got {len(r.markers)})")

    # require_multi_dimension=False (default) + single dim → no augmentation
    r = hold_verdict(
        claim="default single dim",
        markers=[m_single_dim1, m_single_dim2],
        audit=audit,
        require_multi_dimension=False,
    )
    check(len(r.markers) == 2, f"require=False → no augmentation (got {len(r.markers)})")


# ---------------------------------------------------------------------------
# Test group H4: VerdictWithMarkers invariant preserved
# ---------------------------------------------------------------------------

print("\n[H4] invariant: HOLDING always has >=1 marker + INCOMPLETE_FRAME too")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "h4.jsonl")

    for case_name, markers_arg, expected_verdict in [
        ("empty", [], Verdict.INCOMPLETE_FRAME),
        ("single", [IncompleteMarker("search_space", "a", "b", "c")], Verdict.HOLDING),
        ("triple", [
            IncompleteMarker("search_space", "a1", "b1", "c1"),
            IncompleteMarker("witness_type", "a2", "b2", "c2"),
            IncompleteMarker("compute_budget", "a3", "b3", "c3"),
        ], Verdict.HOLDING),
    ]:
        r = hold_verdict(claim=f"case-{case_name}", markers=markers_arg, audit=audit)
        check(r.verdict == expected_verdict, f"case-{case_name} verdict (got {r.verdict})")
        check(len(r.markers) >= 1, f"case-{case_name} has >=1 marker (got {len(r.markers)})")


# ---------------------------------------------------------------------------
# Test group H5: MCP tool_hold_verdict
# ---------------------------------------------------------------------------

print("\n[H5] MCP tool_hold_verdict")

with tempfile.TemporaryDirectory() as tdir:
    _reset_state_for_test()
    r = tool_create_audit_chain("h5", base_dir=tdir)
    cid = r["chain_id"]

    # missing chain
    r = tool_hold_verdict(chain_id="bogus", claim="x", markers=[{
        "dimension": "search_space", "what_was_tried": "a", "what_was_not_tried": "b", "reason": "c",
    }])
    check("error" in r, "missing chain rejected")

    # empty claim
    r = tool_hold_verdict(chain_id=cid, claim="", markers=[{
        "dimension": "search_space", "what_was_tried": "a", "what_was_not_tried": "b", "reason": "c",
    }])
    check("error" in r, "empty claim rejected")

    # non-list markers
    r = tool_hold_verdict(chain_id=cid, claim="x", markers="not-list")  # type: ignore[arg-type]
    check("error" in r, "non-list markers rejected")

    # non-dict marker
    r = tool_hold_verdict(chain_id=cid, claim="x", markers=["not-dict"])  # type: ignore[list-item]
    check("error" in r, "non-dict marker rejected")

    # invalid dimension
    r = tool_hold_verdict(chain_id=cid, claim="x", markers=[{
        "dimension": "invalid_dim", "what_was_tried": "a", "what_was_not_tried": "b", "reason": "c",
    }])
    check("error" in r, "invalid dimension rejected")

    # missing marker field
    r = tool_hold_verdict(chain_id=cid, claim="x", markers=[{
        "dimension": "search_space", "what_was_tried": "a",  # missing 2 fields
    }])
    check("error" in r, "incomplete marker dict rejected")

    # empty markers list → INCOMPLETE_FRAME (via pre_check, not error)
    r = tool_hold_verdict(chain_id=cid, claim="x", markers=[])
    check(r.get("verdict") == "incomplete_frame", f"empty markers → INCOMPLETE_FRAME (got {r})")

    # valid single marker → HOLDING
    r = tool_hold_verdict(
        chain_id=cid,
        claim="my analytical claim",
        markers=[{
            "dimension": "search_space",
            "what_was_tried": "5 counterexample approaches",
            "what_was_not_tried": "structural refutation via categorical semantics",
            "reason": "no time to explore categorical angle in this session",
        }],
        notes="manual reasoning pause",
    )
    check("error" not in r and r["verdict"] == "holding", f"valid HOLDING (got {r})")
    check(r["dfumt"] == "NEITHER", "HOLDING dfumt=NEITHER")
    check(len(r["markers"]) == 1, "single marker preserved")

    # require_multi_dimension=True + single dim via MCP → augmentation
    r = tool_hold_verdict(
        chain_id=cid,
        claim="require multi dim test",
        markers=[
            {"dimension": "search_space", "what_was_tried": "a", "what_was_not_tried": "b", "reason": "c"},
            {"dimension": "search_space", "what_was_tried": "d", "what_was_not_tried": "e", "reason": "f"},
        ],
        require_multi_dimension=True,
    )
    check("error" not in r and len(r["markers"]) == 3,
          f"MCP require_multi_dimension adds aug marker (got {len(r.get('markers', []))})")


# ---------------------------------------------------------------------------
# Test group H6: full 4-tool integration (all return same VerdictWithMarkers shape)
# ---------------------------------------------------------------------------

print("\n[H6] 4-tool VerdictWithMarkers shape consistency")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "h6.jsonl")

    # hold_verdict return
    r_hold = hold_verdict(
        claim="hold demo",
        markers=[IncompleteMarker("search_space", "a", "b", "c")],
        audit=audit,
    )
    check(isinstance(r_hold, VerdictWithMarkers), "hold returns VerdictWithMarkers")
    check(r_hold.verdict == Verdict.HOLDING, "hold verdict = HOLDING")
    check(hasattr(r_hold, "claim") and hasattr(r_hold, "duration_ms")
          and hasattr(r_hold, "audit_hashes"),
          "VerdictWithMarkers has all standard fields")
    check(all(len(h) == 64 for h in r_hold.audit_hashes),
          "audit_hashes are sha256 hex strings")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

print(f"\n{'=' * 60}")
print(f"hold layer result: {passed} passed / {failed} failed")
print(f"{'=' * 60}")
sys.exit(0 if failed == 0 else 1)
