"""
rei-verify refute-3 selftest.

Coverage:
  - Python API pre-check paths: 5 case → INCOMPLETE_FRAME
  - Python API verdict paths: witness / all-pass / mixed-error / time_limit
  - stop_on_first_failure=True vs False semantics
  - compile_predicate_expression var_name extension (ctx binding)
  - MCP tool_assert_breakpoints_explicit: validation + verdict paths + hostile expression
"""
from __future__ import annotations

import sys
import tempfile
import time
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
)
from rei_verify.breakpoint import Breakpoint, assert_breakpoints
from rei_verify.search import compile_predicate_expression, RestrictedExpressionError
from rei_verify.mcp import (
    tool_create_audit_chain,
    tool_assert_breakpoints_explicit,
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
# Test group B1: pre-check paths → INCOMPLETE_FRAME
# ---------------------------------------------------------------------------

print("\n[B1] assert_breakpoints pre-check paths")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "b1.jsonl")

    # empty list
    r = assert_breakpoints(claim="x", breakpoints=[], audit=audit)
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"empty list → INCOMPLETE_FRAME (got {r.verdict})")

    # non-list
    r = assert_breakpoints(claim="x", breakpoints="not-a-list", audit=audit)  # type: ignore[arg-type]
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"non-list → INCOMPLETE_FRAME (got {r.verdict})")

    # item not Breakpoint
    r = assert_breakpoints(claim="x", breakpoints=[{"label": "hi"}], audit=audit)  # type: ignore[list-item]
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"non-Breakpoint items → INCOMPLETE_FRAME (got {r.verdict})")

    # empty label
    r = assert_breakpoints(
        claim="x",
        breakpoints=[Breakpoint(label="", assertion=lambda: True)],
        audit=audit,
    )
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"empty label → INCOMPLETE_FRAME (got {r.verdict})")

    # max_time_sec <= 0
    r = assert_breakpoints(
        claim="x",
        breakpoints=[Breakpoint(label="A", assertion=lambda: True)],
        audit=audit,
        max_time_sec=0.0,
    )
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"max_time_sec=0 → INCOMPLETE_FRAME (got {r.verdict})")


# ---------------------------------------------------------------------------
# Test group B2: verdict paths
# ---------------------------------------------------------------------------

print("\n[B2] verdict paths (witness / all-pass / mixed-error / time_limit)")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "b2.jsonl")

    # witness path
    bps = [
        Breakpoint("case A", lambda: True, {"n": 1}),
        Breakpoint("case B", lambda: False, {"n": 2}),  # this fails
        Breakpoint("case C", lambda: True, {"n": 3}),
    ]
    r = assert_breakpoints(claim="all cases hold", breakpoints=bps, audit=audit)
    check(r.verdict == Verdict.REFUTED, f"witness → REFUTED (got {r.verdict})")
    check(any("case B" in m.reason for m in r.markers),
          f"witness marker names failing breakpoint (got {[m.reason for m in r.markers]})")

    # all pass path
    bps_all = [
        Breakpoint("A", lambda: True),
        Breakpoint("B", lambda: True),
        Breakpoint("C", lambda: True),
    ]
    r = assert_breakpoints(claim="all pass", breakpoints=bps_all, audit=audit)
    check(r.verdict == Verdict.HOLDING, f"all pass → HOLDING (got {r.verdict})")
    check(any(m.dimension == "search_space" for m in r.markers),
          "all-pass has search_space marker")
    check(any("does NOT confirm" in m.reason for m in r.markers),
          "all-pass warns about case exhaustion not equaling proof")

    # mixed error + pass
    def raiser():
        raise ValueError("something broke")

    bps_mixed = [
        Breakpoint("ok", lambda: True),
        Breakpoint("crash", raiser),
        Breakpoint("ok2", lambda: True),
    ]
    r = assert_breakpoints(claim="mixed", breakpoints=bps_mixed, audit=audit)
    check(r.verdict == Verdict.HOLDING, f"mixed pass+error, no fail → HOLDING (got {r.verdict})")
    dims = [m.dimension for m in r.markers]
    check("search_space" in dims and "frame" in dims,
          f"mixed has both search_space + frame markers (got {dims})")


# ---------------------------------------------------------------------------
# Test group B3: stop_on_first_failure semantics
# ---------------------------------------------------------------------------

print("\n[B3] stop_on_first_failure")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "b3.jsonl")

    call_count = [0]
    def counted_true():
        call_count[0] += 1
        return True
    def counted_false():
        call_count[0] += 1
        return False

    # stop_on_first_failure=True: after fail at 2nd, 3rd should NOT run
    call_count[0] = 0
    bps = [
        Breakpoint("A", counted_true),
        Breakpoint("B", counted_false),
        Breakpoint("C", counted_true),
    ]
    r = assert_breakpoints(claim="x", breakpoints=bps, audit=audit, stop_on_first_failure=True)
    check(r.verdict == Verdict.REFUTED, "witness with stop=True")
    check(call_count[0] == 2, f"stop_on_first_failure=True → 2 calls (got {call_count[0]})")

    # stop_on_first_failure=False: still REFUTED but all breakpoints executed
    call_count[0] = 0
    bps = [
        Breakpoint("A", counted_true),
        Breakpoint("B", counted_false),
        Breakpoint("C", counted_true),
    ]
    r = assert_breakpoints(claim="x", breakpoints=bps, audit=audit, stop_on_first_failure=False)
    check(r.verdict == Verdict.REFUTED, "witness with stop=False still REFUTED")
    check(call_count[0] == 3, f"stop_on_first_failure=False → 3 calls (got {call_count[0]})")


# ---------------------------------------------------------------------------
# Test group B4: time_limit
# ---------------------------------------------------------------------------

print("\n[B4] time_limit budget")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "b4.jsonl")

    def slow():
        time.sleep(0.15)
        return True

    bps = [Breakpoint(f"slow_{i}", slow) for i in range(10)]
    r = assert_breakpoints(claim="all slow pass", breakpoints=bps, audit=audit, max_time_sec=0.3)
    check(r.verdict == Verdict.HOLDING, f"time budget → HOLDING (got {r.verdict})")
    dims = [m.dimension for m in r.markers]
    check("compute_budget" in dims, f"time budget → compute_budget marker (got {dims})")


# ---------------------------------------------------------------------------
# Test group B5: compile_predicate_expression var_name extension
# ---------------------------------------------------------------------------

print("\n[B5] compile_predicate_expression var_name")

# default x
p = compile_predicate_expression("x > 0")
check(p(5) is True and p(-1) is False, "default var_name='x' works")

# ctx binding
p = compile_predicate_expression("ctx['n'] > 0 and ctx['label'] == 'ok'", var_name="ctx")
check(p({"n": 5, "label": "ok"}) is True, "ctx var_name works with dict binding")
check(p({"n": 0, "label": "ok"}) is False, "ctx var_name returns False when predicate fails")

# invalid var_name
try:
    compile_predicate_expression("x > 0", var_name="")
    check(False, "empty var_name should reject")
except RestrictedExpressionError:
    check(True, "empty var_name rejected")

try:
    compile_predicate_expression("x > 0", var_name="not an identifier")
    check(False, "non-identifier var_name should reject")
except RestrictedExpressionError:
    check(True, "non-identifier var_name rejected")


# ---------------------------------------------------------------------------
# Test group B6: MCP tool_assert_breakpoints_explicit
# ---------------------------------------------------------------------------

print("\n[B6] MCP tool_assert_breakpoints_explicit")

with tempfile.TemporaryDirectory() as tdir:
    _reset_state_for_test()
    r = tool_create_audit_chain("b6", base_dir=tdir)
    cid = r["chain_id"]

    # witness via MCP
    r = tool_assert_breakpoints_explicit(
        chain_id=cid,
        claim="collatz t1=1 descent",
        breakpoints=[
            {"label": "n=27 case", "assertion_expr": "ctx['descent'] < 0",
             "context": {"n": 27, "descent": -0.5}},
            {"label": "n=703 case", "assertion_expr": "ctx['descent'] < 0",
             "context": {"n": 703, "descent": 0.2}},  # this fails
            {"label": "n=6171 case", "assertion_expr": "ctx['descent'] < 0",
             "context": {"n": 6171, "descent": -0.3}},
        ],
    )
    check("error" not in r and r["verdict"] == "refuted",
          f"MCP witness → REFUTED (got verdict={r.get('verdict')}, error={r.get('error')})")
    check(any("n=703" in m["reason"] or "703" in m["reason"] for m in r["markers"]),
          f"failing breakpoint identified in markers")

    # all pass via MCP
    r = tool_assert_breakpoints_explicit(
        chain_id=cid,
        claim="finite bounded claim",
        breakpoints=[
            {"label": "n=1", "assertion_expr": "ctx['x'] > 0", "context": {"x": 1}},
            {"label": "n=2", "assertion_expr": "ctx['x'] > 0", "context": {"x": 2}},
        ],
    )
    check("error" not in r and r["verdict"] == "holding",
          f"MCP all pass → HOLDING (got {r})")
    check(r["dfumt"] == "NEITHER", "HOLDING dfumt=NEITHER")

    # empty claim
    r = tool_assert_breakpoints_explicit(chain_id=cid, claim="", breakpoints=[
        {"label": "A", "assertion_expr": "True"},
    ])
    check("error" in r, "empty claim rejected")

    # non-list breakpoints
    r = tool_assert_breakpoints_explicit(chain_id=cid, claim="x", breakpoints="not-list")  # type: ignore[arg-type]
    check("error" in r, "non-list breakpoints rejected")

    # bp missing label
    r = tool_assert_breakpoints_explicit(chain_id=cid, claim="x", breakpoints=[
        {"assertion_expr": "True"},
    ])
    check("error" in r and "label" in r["error"], "missing label rejected")

    # bp missing assertion_expr
    r = tool_assert_breakpoints_explicit(chain_id=cid, claim="x", breakpoints=[
        {"label": "A"},
    ])
    check("error" in r and "assertion_expr" in r["error"], "missing assertion_expr rejected")

    # hostile expression
    r = tool_assert_breakpoints_explicit(chain_id=cid, claim="x", breakpoints=[
        {"label": "evil", "assertion_expr": "__import__('os').system('echo pwn')"},
    ])
    check("error" in r and "forbidden" in r["error"].lower(),
          f"hostile expression rejected via MCP (got {r})")

    # empty list → INCOMPLETE_FRAME (routed through pre_check, not error)
    r = tool_assert_breakpoints_explicit(chain_id=cid, claim="x", breakpoints=[])
    check(r.get("verdict") == "incomplete_frame",
          f"empty list → INCOMPLETE_FRAME verdict (got {r})")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

print(f"\n{'=' * 60}")
print(f"breakpoint layer result: {passed} passed / {failed} failed")
print(f"{'=' * 60}")
sys.exit(0 if failed == 0 else 1)
