"""
rei-verify skeleton selftest (25+ assertions)。

Coverage:
  - Verdict 4 値 全て 到達
  - IncompleteMarker invariant (dimension 語彙 / 非空 check)
  - PostCheckResult invariant (refuted=True は witness 必須)
  - VerdictWithMarkers invariant (CONFIRMED 以外 marker 必須)
  - VerifiedExecution: pre_check False / pre_check raise / action raise / post_check raise / all success paths
  - AuditChain: append hash / verify integrity / tamper detection / genesis hash / restart restore
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# force UTF-8 stdout (Windows cp932 対策)
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# scratchpad layout: src/rei_verify → path insertion
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / "src"))

from rei_verify import (
    Verdict,
    IncompleteMarker,
    PostCheckResult,
    VerdictWithMarkers,
    VerifiedExecution,
    AuditChain,
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


def expect_raises(exc_type, callable_, *args, **kwargs) -> None:
    global passed, failed
    try:
        callable_(*args, **kwargs)
    except exc_type:
        passed += 1
        return
    except BaseException as e:
        failed += 1
        print(f"  FAIL: expected {exc_type.__name__}, got {type(e).__name__}: {e}")
        return
    failed += 1
    print(f"  FAIL: expected {exc_type.__name__}, no exception raised")


# ---------------------------------------------------------------------------
# Test group 1: IncompleteMarker invariants
# ---------------------------------------------------------------------------

print("\n[G1] IncompleteMarker invariants")

m_ok = IncompleteMarker(
    dimension="search_space",
    what_was_tried="n in [1, 100]",
    what_was_not_tried="n > 100",
    reason="budget cutoff",
)
check(m_ok.dimension == "search_space", "well-formed marker constructs")
check(m_ok.to_dict() == {
    "dimension": "search_space",
    "what_was_tried": "n in [1, 100]",
    "what_was_not_tried": "n > 100",
    "reason": "budget cutoff",
}, "to_dict roundtrip")

# invalid dimension
expect_raises(
    ValueError,
    IncompleteMarker,
    dimension="something_else", what_was_tried="x", what_was_not_tried="y", reason="z",
)

# empty fields
expect_raises(
    ValueError,
    IncompleteMarker,
    dimension="frame", what_was_tried="", what_was_not_tried="y", reason="z",
)
expect_raises(
    ValueError,
    IncompleteMarker,
    dimension="frame", what_was_tried="x", what_was_not_tried="", reason="z",
)
expect_raises(
    ValueError,
    IncompleteMarker,
    dimension="frame", what_was_tried="x", what_was_not_tried="y", reason="",
)


# ---------------------------------------------------------------------------
# Test group 2: PostCheckResult invariants
# ---------------------------------------------------------------------------

print("\n[G2] PostCheckResult invariants")

pr_confirmed = PostCheckResult(refuted=False, markers=[])
check(pr_confirmed.refuted is False and pr_confirmed.markers == [], "CONFIRMED-style PostCheckResult")

pr_holding = PostCheckResult(
    refuted=False,
    markers=[IncompleteMarker("compute_budget", "10s", "beyond 10s", "wall-clock cutoff")],
)
check(pr_holding.refuted is False and len(pr_holding.markers) == 1, "HOLDING-style PostCheckResult")

pr_refuted = PostCheckResult(
    refuted=True,
    markers=[IncompleteMarker("witness_type", "structural check", "n=42 counter", "n=42 breaks")],
)
check(pr_refuted.refuted is True, "REFUTED-style PostCheckResult")

# refuted=True requires witness
expect_raises(
    ValueError,
    PostCheckResult, refuted=True, markers=[],
)


# ---------------------------------------------------------------------------
# Test group 3: AuditChain hash chain integrity
# ---------------------------------------------------------------------------

print("\n[G3] AuditChain hash chain integrity")

with tempfile.TemporaryDirectory() as tdir:
    chain_path = Path(tdir) / "audit.jsonl"
    ac = AuditChain(chain_path)

    # empty state
    v = ac.verify()
    check(v.ok and v.entry_count == 0 and v.broken_at is None, "empty chain verify OK")

    h1 = ac.append({"phase": "test", "n": 1})
    h2 = ac.append({"phase": "test", "n": 2})
    h3 = ac.append({"phase": "test", "n": 3})
    check(h1 != h2 != h3, "distinct hashes for distinct entries")
    check(len(h1) == 64 and all(c in "0123456789abcdef" for c in h1), "sha256 hex format")

    v = ac.verify()
    check(v.ok and v.entry_count == 3 and v.broken_at is None, "3-entry chain verify OK")
    check(v.last_hash == h3, "last_hash matches most recent append")

    # tamper: rewrite line 1 (index 1) with fake entry
    lines = chain_path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["entry"] = {"phase": "tampered", "n": 999}
    lines[1] = json.dumps(tampered, ensure_ascii=False, sort_keys=True)
    chain_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    v = ac.verify()
    check(not v.ok, "tamper detected → verify.ok == False")
    check(v.broken_at == 1, f"broken_at points to tampered line (expected 1, got {v.broken_at})")
    check("hash mismatch" in v.reason or "tamper" in v.reason, "reason mentions hash mismatch")


# ---------------------------------------------------------------------------
# Test group 3b: findings ② top-level key injection detection (0.1.0a2 fix)
# ---------------------------------------------------------------------------

print("\n[G3b] AuditChain top-level key injection (0.1.0a2 findings ② regression)")

with tempfile.TemporaryDirectory() as tdir:
    chain_path = Path(tdir) / "injection.jsonl"
    ac = AuditChain(chain_path)
    for i in range(5):
        ac.append({"phase": "test", "step": i})

    v = ac.verify()
    check(v.ok and v.entry_count == 5, "5-entry clean chain verify OK before injection")

    # Inject unexpected top-level key into line index 2
    lines = chain_path.read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[2])
    obj["note"] = "INJECTED_AT_TOPLEVEL"        # entry の外側に追加キー
    lines[2] = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    chain_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    v = ac.verify()
    check(not v.ok, f"top-level key injection detected → verify.ok == False (got {v.ok})")
    check(v.broken_at == 2, f"broken_at points to injected line (expected 2, got {v.broken_at})")
    check(
        "unexpected keys" in v.reason or "line-object key set" in v.reason,
        f"reason mentions key set mismatch (got {v.reason!r})",
    )
    # ★ pre-0.1.0a2 (findings ②) では 素通り = ok=True だった。 0.1.0a2 で 検出可能に。

    # 別 tampering: hash_version 欠落 chain (pre-0.1.0a2 format 検出)
    old_format_path = Path(tdir) / "old_format.jsonl"
    old_line = {"seq": 0, "prev_hash": "0" * 64, "entry": {"legacy": True}, "hash": "abcd" * 16}
    old_format_path.write_text(json.dumps(old_line, sort_keys=True) + "\n", encoding="utf-8")
    ac_old = AuditChain(old_format_path)
    v = ac_old.verify()
    check(
        not v.ok and "hash_version key missing" in v.reason,
        f"pre-0.1.0a2 format detected with clear error (got {v.reason!r})",
    )


# ---------------------------------------------------------------------------
# Test group 4: AuditChain restart restore + genesis
# ---------------------------------------------------------------------------

print("\n[G4] AuditChain restart restore")

with tempfile.TemporaryDirectory() as tdir:
    chain_path = Path(tdir) / "restart.jsonl"
    ac1 = AuditChain(chain_path)
    ac1.append({"phase": "A", "n": 1})
    ac1.append({"phase": "B", "n": 2})

    # new instance on same file
    ac2 = AuditChain(chain_path)
    h_next = ac2.append({"phase": "C", "n": 3})
    v = ac2.verify()
    check(v.ok and v.entry_count == 3, f"restart preserves chain (got {v.entry_count} entries, ok={v.ok})")
    check(v.last_hash == h_next, "last_hash after restart matches new append")


# ---------------------------------------------------------------------------
# Test group 5: VerifiedExecution — all 4 Verdicts reachable
# ---------------------------------------------------------------------------

print("\n[G5] VerifiedExecution 4 verdicts")

with tempfile.TemporaryDirectory() as tdir:
    ac = AuditChain(Path(tdir) / "ve1.jsonl")

    # CONFIRMED path
    ve = VerifiedExecution(
        claim="1 + 1 == 2",
        pre_check=lambda: True,
        post_check=lambda r: PostCheckResult(refuted=(r != 2), markers=(
            [] if r == 2 else [IncompleteMarker("witness_type", "arithmetic", f"got {r}", "wrong result")]
        )),
        audit=ac,
    )
    r = ve.run(lambda: 1 + 1)
    check(r.verdict == Verdict.CONFIRMED, f"CONFIRMED verdict (got {r.verdict})")
    check(r.markers == [], "CONFIRMED has no markers")
    check(len(r.audit_hashes) >= 4, f"audit hashes >= 4 (got {len(r.audit_hashes)})")

    # REFUTED path
    ve2 = VerifiedExecution(
        claim="1 + 1 == 3",
        pre_check=lambda: True,
        post_check=lambda r: PostCheckResult(
            refuted=(r != 3),
            markers=[IncompleteMarker("witness_type", "arithmetic 1+1", f"got {r}, expected 3", "arithmetic contradicts claim")],
        ),
        audit=ac,
    )
    r = ve2.run(lambda: 1 + 1)
    check(r.verdict == Verdict.REFUTED, f"REFUTED verdict (got {r.verdict})")
    check(len(r.markers) == 1, "REFUTED has witness marker")

    # HOLDING path
    ve3 = VerifiedExecution(
        claim="all_n_have_property_X",
        pre_check=lambda: True,
        post_check=lambda r: PostCheckResult(
            refuted=False,
            markers=[IncompleteMarker(
                "search_space", f"tried n in [1, {r}]",
                f"n > {r} not searched", "budget cutoff at 100",
            )],
        ),
        audit=ac,
    )
    r = ve3.run(lambda: 100)
    check(r.verdict == Verdict.HOLDING, f"HOLDING verdict (got {r.verdict})")
    check(len(r.markers) == 1 and r.markers[0].dimension == "search_space", "HOLDING has search_space marker")

    # INCOMPLETE_FRAME path (pre_check False)
    ve4 = VerifiedExecution(
        claim="frame not established",
        pre_check=lambda: False,
        post_check=lambda r: PostCheckResult(refuted=False, markers=[]),
        audit=ac,
    )
    r = ve4.run(lambda: 42)
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"INCOMPLETE_FRAME verdict (got {r.verdict})")
    check(any(m.dimension == "frame" for m in r.markers), "INCOMPLETE_FRAME has frame marker")


# ---------------------------------------------------------------------------
# Test group 6: VerifiedExecution — exception paths
# ---------------------------------------------------------------------------

print("\n[G6] VerifiedExecution exception paths")

with tempfile.TemporaryDirectory() as tdir:
    ac = AuditChain(Path(tdir) / "ve2.jsonl")

    # pre_check raises → INCOMPLETE_FRAME
    def _bad_pre():
        raise RuntimeError("pre_check bug")

    ve = VerifiedExecution(
        claim="anything",
        pre_check=_bad_pre,
        post_check=lambda r: PostCheckResult(refuted=False, markers=[]),
        audit=ac,
    )
    r = ve.run(lambda: 1)
    check(r.verdict == Verdict.INCOMPLETE_FRAME, f"pre_check raise → INCOMPLETE_FRAME (got {r.verdict})")

    # action raises → default classifier = REFUTED
    def _bad_action():
        raise ValueError("action failed")

    ve = VerifiedExecution(
        claim="action always succeeds",
        pre_check=lambda: True,
        post_check=lambda r: PostCheckResult(refuted=False, markers=[]),
        audit=ac,
    )
    r = ve.run(_bad_action)
    check(r.verdict == Verdict.REFUTED, f"action raise → REFUTED (got {r.verdict})")
    check(any("action failed" in m.reason for m in r.markers), "exception witness in marker")

    # post_check raises → HOLDING (post_check itself is incomplete)
    def _bad_post(_result):
        raise RuntimeError("post_check bug")

    ve = VerifiedExecution(
        claim="something",
        pre_check=lambda: True,
        post_check=_bad_post,
        audit=ac,
    )
    r = ve.run(lambda: 1)
    check(r.verdict == Verdict.HOLDING, f"post_check raise → HOLDING (got {r.verdict})")


# ---------------------------------------------------------------------------
# Test group 7: VerdictWithMarkers invariant
# ---------------------------------------------------------------------------

print("\n[G7] VerdictWithMarkers invariant (沈黙を成功と偽装しない)")

# HOLDING with no markers → ValueError
expect_raises(
    ValueError,
    VerdictWithMarkers,
    verdict=Verdict.HOLDING, markers=[], audit_hashes=[], claim="x",
    started_at="2026-08-19T00:00:00+00:00", ended_at="2026-08-19T00:00:01+00:00", duration_ms=1000.0,
)

# REFUTED with no markers → ValueError
expect_raises(
    ValueError,
    VerdictWithMarkers,
    verdict=Verdict.REFUTED, markers=[], audit_hashes=[], claim="x",
    started_at="2026-08-19T00:00:00+00:00", ended_at="2026-08-19T00:00:01+00:00", duration_ms=1000.0,
)

# INCOMPLETE_FRAME with no markers → ValueError
expect_raises(
    ValueError,
    VerdictWithMarkers,
    verdict=Verdict.INCOMPLETE_FRAME, markers=[], audit_hashes=[], claim="x",
    started_at="2026-08-19T00:00:00+00:00", ended_at="2026-08-19T00:00:01+00:00", duration_ms=1000.0,
)

# CONFIRMED with no markers → OK
ok = VerdictWithMarkers(
    verdict=Verdict.CONFIRMED, markers=[], audit_hashes=["h"], claim="x",
    started_at="2026-08-19T00:00:00+00:00", ended_at="2026-08-19T00:00:01+00:00", duration_ms=1000.0,
)
check(ok.verdict == Verdict.CONFIRMED, "CONFIRMED without markers is valid")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

print(f"\n{'=' * 60}")
print(f"result: {passed} passed / {failed} failed")
print(f"{'=' * 60}")
sys.exit(0 if failed == 0 else 1)
