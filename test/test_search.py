"""
rei-verify refute-2 selftest.

Coverage:
  - Python API pre-check paths: 5 case → INCOMPLETE_FRAME
  - Python API 4 exit path: witness / exhausted / time_limit / sample_limit
  - Python API predicate error (per-sample) → skipped + counted in marker
  - MCP tool restricted eval: safe expression + hostile expression rejection
  - MCP tool 4 exit path 通し verify
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
from rei_verify.search import (
    search_counterexample,
    compile_predicate_expression,
    RestrictedExpressionError,
)
from rei_verify.mcp import (
    tool_create_audit_chain,
    tool_search_counterexample_explicit,
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
# Test group S1: pre-check paths → INCOMPLETE_FRAME
# ---------------------------------------------------------------------------

print("\n[S1] search_counterexample pre-check paths")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "s1.jsonl")

    # predicate not callable
    r = search_counterexample(
        claim="x", predicate="not-a-callable", space=[1, 2, 3],  # type: ignore[arg-type]
        audit=audit,
    )
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"non-callable predicate → INCOMPLETE_FRAME (got {r.verdict})")

    # space is None
    r = search_counterexample(
        claim="x", predicate=lambda x: True, space=None,  # type: ignore[arg-type]
        audit=audit,
    )
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"None space → INCOMPLETE_FRAME (got {r.verdict})")

    # space not iterable
    r = search_counterexample(
        claim="x", predicate=lambda x: True, space=42,  # type: ignore[arg-type]
        audit=audit,
    )
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"non-iterable space → INCOMPLETE_FRAME (got {r.verdict})")

    # max_samples <= 0
    r = search_counterexample(
        claim="x", predicate=lambda x: True, space=[1], audit=audit,
        max_samples=0,
    )
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"max_samples=0 → INCOMPLETE_FRAME (got {r.verdict})")

    # max_time_sec <= 0
    r = search_counterexample(
        claim="x", predicate=lambda x: True, space=[1], audit=audit,
        max_time_sec=0.0,
    )
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"max_time_sec=0 → INCOMPLETE_FRAME (got {r.verdict})")


# ---------------------------------------------------------------------------
# Test group S2: 4 exit path
# ---------------------------------------------------------------------------

print("\n[S2] 4 exit path (witness / exhausted / time / sample)")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "s2.jsonl")

    # witness path: predicate returns True on n=42
    r = search_counterexample(
        claim="no n in [1,100] equals 42",
        predicate=lambda x: x == 42,
        space=range(1, 101),
        audit=audit,
        space_description="range(1, 101)",
    )
    check(r.verdict == Verdict.REFUTED, f"witness → REFUTED (got {r.verdict})")
    check(len(r.markers) == 1 and "witness_type" == r.markers[0].dimension,
          "REFUTED has witness_type marker")
    check("42" in r.markers[0].reason, f"witness value 42 in reason (got {r.markers[0].reason!r})")

    # exhausted path: no counter-example in finite space
    r = search_counterexample(
        claim="all n in [1,10] are positive",
        predicate=lambda x: x <= 0,   # never true for n in [1,10]
        space=range(1, 11),
        audit=audit,
        space_description="range(1, 11)",
    )
    check(r.verdict == Verdict.HOLDING, f"exhausted → HOLDING (got {r.verdict})")
    check(len(r.markers) == 1 and r.markers[0].dimension == "search_space",
          f"exhausted has search_space marker (got {r.markers[0].dimension})")
    check("finite" in r.markers[0].reason and "does NOT confirm" in r.markers[0].reason,
          "exhausted marker warns absence ≠ proof")

    # sample_limit path: infinite generator + tight max_samples
    def infinite_gen():
        n = 0
        while True:
            n += 1
            yield n

    r = search_counterexample(
        claim="all naturals",
        predicate=lambda x: x > 10**9,   # true for very large n only
        space=infinite_gen(),
        audit=audit,
        max_samples=100,
        space_description="infinite naturals",
    )
    check(r.verdict == Verdict.HOLDING, f"sample_limit → HOLDING (got {r.verdict})")
    check(any(m.dimension == "compute_budget" for m in r.markers),
          f"sample_limit has compute_budget marker (got {[m.dimension for m in r.markers]})")

    # time_limit path: slow predicate + tight time
    def slow_pred(x):
        time.sleep(0.05)
        return False
    r = search_counterexample(
        claim="slow",
        predicate=slow_pred,
        space=range(1, 1000),
        audit=audit,
        max_time_sec=0.2,   # ~4 samples per 0.2s
        space_description="range(1,1000) with 50ms predicate",
    )
    check(r.verdict == Verdict.HOLDING, f"time_limit → HOLDING (got {r.verdict})")
    check(any(m.dimension == "compute_budget" for m in r.markers),
          "time_limit has compute_budget marker")


# ---------------------------------------------------------------------------
# Test group S3: predicate error (per-sample) counted
# ---------------------------------------------------------------------------

print("\n[S3] predicate per-sample error handling")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "s3.jsonl")

    def sometimes_raise(x):
        if x == 5:
            raise ValueError("bad sample 5")
        return x == 999  # never in [1,10]

    r = search_counterexample(
        claim="no 999 in [1,10]",
        predicate=sometimes_raise,
        space=range(1, 11),
        audit=audit,
    )
    check(r.verdict == Verdict.HOLDING, f"per-sample error skipped, no witness → HOLDING (got {r.verdict})")
    # markers should include exhausted marker + a frame marker for predicate errors
    dims = [m.dimension for m in r.markers]
    check("search_space" in dims and "frame" in dims,
          f"markers cover both exhausted + predicate error (got {dims})")


# ---------------------------------------------------------------------------
# Test group S4: compile_predicate_expression safety
# ---------------------------------------------------------------------------

print("\n[S4] restricted expression safety")

# valid arithmetic expression
p = compile_predicate_expression("x % 2 == 0")
check(p(4) is True and p(5) is False, "simple % expression works")

p = compile_predicate_expression("abs(x) > 100 and x < 0")
check(p(-150) is True and p(-50) is False and p(150) is False,
      "compound expression with allowed builtins")

# forbidden tokens
forbidden_exprs = [
    "__import__('os').system('echo pwn')",
    "x.__class__",
    "exec('print(1)')",
    "eval('1+1')",
    "open('/etc/passwd').read()",
    "compile('1', '', 'eval')",
    "globals()",
    "locals()",
]
for bad in forbidden_exprs:
    try:
        compile_predicate_expression(bad)
        check(False, f"expected rejection of {bad!r}")
    except RestrictedExpressionError:
        check(True, f"rejected: {bad!r}")

# empty / non-string
try:
    compile_predicate_expression("")
    check(False, "empty expression should reject")
except RestrictedExpressionError:
    check(True, "empty expression rejected")

# syntax error
try:
    compile_predicate_expression("x +* 2")
    check(False, "syntax error should reject")
except RestrictedExpressionError:
    check(True, "syntax error rejected")


# ---------------------------------------------------------------------------
# Test group S5: MCP tool_search_counterexample_explicit
# ---------------------------------------------------------------------------

print("\n[S5] MCP tool_search_counterexample_explicit")

with tempfile.TemporaryDirectory() as tdir:
    _reset_state_for_test()
    r = tool_create_audit_chain("s5", base_dir=tdir)
    cid = r["chain_id"]

    # witness via MCP
    r = tool_search_counterexample_explicit(
        chain_id=cid,
        claim="no n in [1,50] equals 30",
        samples=list(range(1, 51)),
        predicate_expr="x == 30",
        space_description="list(range(1,51))",
    )
    check("error" not in r and r["verdict"] == "refuted",
          f"MCP witness → REFUTED (got {r})")
    check(r["dfumt"] == "FALSE", "REFUTED maps dfumt=FALSE")

    # exhausted via MCP
    r = tool_search_counterexample_explicit(
        chain_id=cid,
        claim="no odd in [2,4,6,8]",
        samples=[2, 4, 6, 8],
        predicate_expr="x % 2 == 1",
    )
    check("error" not in r and r["verdict"] == "holding",
          f"MCP exhausted → HOLDING (got {r})")
    check(r["dfumt"] == "NEITHER", "HOLDING maps dfumt=NEITHER")

    # empty claim
    r = tool_search_counterexample_explicit(
        chain_id=cid, claim="", samples=[1, 2], predicate_expr="x > 0",
    )
    check("error" in r, "empty claim rejected")

    # non-list samples
    r = tool_search_counterexample_explicit(
        chain_id=cid, claim="x", samples="not-list",  # type: ignore[arg-type]
        predicate_expr="x > 0",
    )
    check("error" in r, "non-list samples rejected")

    # hostile expression via MCP (defense in depth)
    r = tool_search_counterexample_explicit(
        chain_id=cid, claim="x", samples=[1, 2],
        predicate_expr="__import__('os').system('echo pwn')",
    )
    check("error" in r and "forbidden" in r["error"].lower(),
          f"hostile expression rejected via MCP (got {r})")

    # missing chain
    r = tool_search_counterexample_explicit(
        chain_id="bogus", claim="x", samples=[1], predicate_expr="x > 0",
    )
    check("error" in r, "missing chain rejected")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

print(f"\n{'=' * 60}")
print(f"search layer result: {passed} passed / {failed} failed")
print(f"{'=' * 60}")
sys.exit(0 if failed == 0 else 1)
