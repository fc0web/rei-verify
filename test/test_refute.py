"""
rei-verify refute-1 selftest。

Coverage:
  - parse_lean_axioms: 6 pattern (no-axiom / axiom list / quotes variant / missing / multiline / different theorem)
  - classify_axioms: 5 case (empty / all-allowed / sorry / native_decide / disallowed)
  - refute_lean_source pre-check paths: 4 case (source 空 / theorem 名 空 / lean_bin 不在 / theorem 名 が source に無い) → INCOMPLETE_FRAME
  - live smoke (if `lean` on PATH): 3 case (axiom-free proof / sorry proof / build error)
"""
from __future__ import annotations

import shutil
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
)
from rei_verify.refute import (
    parse_lean_axioms,
    classify_axioms,
    refute_lean_source,
    DEFAULT_ALLOWED_AXIOMS,
    AxiomParseResult,
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
# Test group R1: parse_lean_axioms
# ---------------------------------------------------------------------------

print("\n[R1] parse_lean_axioms")

# no-axiom
r = parse_lean_axioms(
    "'my_thm' does not depend on any axioms\n",
    "my_thm",
)
check(r.axioms == frozenset(), f"no-axiom → empty set (got {r.axioms})")

# axiom list
r = parse_lean_axioms(
    "'my_thm' depends on axioms: [propext, Classical.choice, Quot.sound]\n",
    "my_thm",
)
check(r.axioms == frozenset({"propext", "Classical.choice", "Quot.sound"}),
      f"axiom list parsed (got {r.axioms})")

# missing (parser failure = None)
r = parse_lean_axioms(
    "some unrelated stdout without axiom line\n",
    "my_thm",
)
check(r.axioms is None, f"missing pattern → None (got {r.axioms})")

# multiline axiom list
r = parse_lean_axioms(
    "'thm2' depends on axioms: [propext,\n Classical.choice,\n MyAxiom]\n",
    "thm2",
)
check(r.axioms == frozenset({"propext", "Classical.choice", "MyAxiom"}),
      f"multiline parsed (got {r.axioms})")

# different theorem name (correct isolation)
r = parse_lean_axioms(
    "'thm_A' depends on axioms: [propext]\n'thm_B' depends on axioms: [Classical.choice]\n",
    "thm_B",
)
check(r.axioms == frozenset({"Classical.choice"}),
      f"correct theorem picked (got {r.axioms})")

# backtick quoted variant (some lean versions use backticks)
r = parse_lean_axioms(
    "`my_thm` depends on axioms: [propext]\n",
    "my_thm",
)
check(r.axioms == frozenset({"propext"}), f"backtick variant parsed (got {r.axioms})")


# ---------------------------------------------------------------------------
# Test group R2: classify_axioms
# ---------------------------------------------------------------------------

print("\n[R2] classify_axioms")

# empty axioms → no markers (CONFIRMED)
m = classify_axioms(frozenset())
check(m == [], f"empty axioms → no markers (got {len(m)})")

# all-allowed → no markers
m = classify_axioms(frozenset({"propext", "Classical.choice", "Quot.sound"}))
check(m == [], f"all Mathlib base allowed → no markers (got {len(m)})")

# sorry present
m = classify_axioms(frozenset({"propext", "sorryAx"}))
check(len(m) == 1 and "sorryAx" in m[0].reason, f"sorry detected (got {m})")

# native_decide present
m = classify_axioms(frozenset({"propext", "Lean.ofReduceBool"}))
check(len(m) == 1 and "native_decide" in m[0].reason,
      f"native_decide detected (got {m})")

# disallowed axiom present
m = classify_axioms(frozenset({"propext", "MyCustomAxiom"}))
check(len(m) == 1 and "MyCustomAxiom" in m[0].reason,
      f"disallowed axiom detected (got {m})")

# multiple issues at once
m = classify_axioms(frozenset({"propext", "sorryAx", "Lean.ofReduceBool", "MyAxiom"}))
check(len(m) == 3, f"multiple issues each own marker (got {len(m)})")

# custom allowed_axioms
m = classify_axioms(
    frozenset({"MyAxiom", "propext"}),
    allowed=frozenset({"MyAxiom", "propext", "Classical.choice", "Quot.sound"}),
)
check(m == [], f"custom allowed_axioms honored (got {m})")


# ---------------------------------------------------------------------------
# Test group R3: refute_lean_source pre-check paths (mock 不要)
# ---------------------------------------------------------------------------

print("\n[R3] refute_lean_source pre-check paths → INCOMPLETE_FRAME")

with tempfile.TemporaryDirectory() as tdir:
    audit = AuditChain(Path(tdir) / "r3.jsonl")

    # source 空
    r = refute_lean_source(
        claim="empty source",
        lean_source="",
        audit=audit,
        theorem_name="my_thm",
    )
    check(r.verdict == Verdict.INCOMPLETE_FRAME,
          f"empty source → INCOMPLETE_FRAME (got {r.verdict})")

    # theorem 名 空
    r = refute_lean_source(
        claim="empty theorem name",
        lean_source="theorem x : True := trivial",
        audit=audit,
        theorem_name="",
    )
    check(r.verdict == Verdict.INCOMPLETE_FRAME,
          f"empty theorem name → INCOMPLETE_FRAME (got {r.verdict})")

    # lean_bin explicitly non-existent
    r = refute_lean_source(
        claim="lean bin missing",
        lean_source="theorem x : True := trivial",
        audit=audit,
        theorem_name="x",
        lean_bin="/nonexistent/path/to/lean-fake",
    )
    check(r.verdict == Verdict.INCOMPLETE_FRAME,
          f"lean_bin missing → INCOMPLETE_FRAME (got {r.verdict})")

    # theorem 名 が source に含まれない
    r = refute_lean_source(
        claim="theorem name mismatch",
        lean_source="theorem foo : True := trivial",
        audit=audit,
        theorem_name="bar",
    )
    check(r.verdict == Verdict.INCOMPLETE_FRAME,
          f"theorem name not in source → INCOMPLETE_FRAME (got {r.verdict})")

    # markers all present (invariant enforcement)
    check(all(len(x.markers) >= 1 for x in [r]),
          "INCOMPLETE_FRAME always has at least 1 marker")


# ---------------------------------------------------------------------------
# Test group R4: live smoke (Lean 4 required — skip if absent)
# ---------------------------------------------------------------------------

print("\n[R4] live smoke (lean 4 subprocess)")

lean_exe = shutil.which("lean")
if lean_exe is None:
    print("  SKIP: lean binary not on PATH")
else:
    with tempfile.TemporaryDirectory() as tdir:
        audit = AuditChain(Path(tdir) / "r4.jsonl")

        # (1) axiom-free trivial theorem → CONFIRMED (or HOLDING if base axioms needed)
        r = refute_lean_source(
            claim="True is True",
            lean_source="theorem trivial_true : True := trivial\n",
            audit=audit,
            theorem_name="trivial_true",
            timeout_sec=60,
        )
        print(f"    smoke-1 (trivial True): verdict={r.verdict.value}, markers={len(r.markers)}, dur={r.duration_ms:.0f}ms")
        check(r.verdict in (Verdict.CONFIRMED, Verdict.HOLDING),
              f"trivial True → CONFIRMED or HOLDING (got {r.verdict})")

        # (2) sorry proof → HOLDING (sorry marker)
        r = refute_lean_source(
            claim="hard claim (never proved)",
            lean_source="theorem hard : ∀ n : Nat, n + 0 = n := by sorry\n",
            audit=audit,
            theorem_name="hard",
            timeout_sec=60,
        )
        print(f"    smoke-2 (sorry proof): verdict={r.verdict.value}, markers={len(r.markers)}")
        check(r.verdict == Verdict.HOLDING,
              f"sorry proof → HOLDING (got {r.verdict})")
        check(any("sorry" in m.reason.lower() for m in r.markers),
              "sorry marker present")

        # (3) build error (syntax) → REFUTED
        r = refute_lean_source(
            claim="broken syntax",
            lean_source="theorem broken : True := this_is_not_a_valid_term\n",
            audit=audit,
            theorem_name="broken",
            timeout_sec=60,
        )
        print(f"    smoke-3 (broken syntax): verdict={r.verdict.value}, markers={len(r.markers)}")
        check(r.verdict == Verdict.REFUTED,
              f"build error → REFUTED (got {r.verdict})")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

print(f"\n{'=' * 60}")
print(f"refute layer result: {passed} passed / {failed} failed")
print(f"{'=' * 60}")
sys.exit(0 if failed == 0 else 1)
