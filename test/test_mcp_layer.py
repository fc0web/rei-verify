"""
rei-verify MCP layer selftest。

MCP プロトコル は 呼ばず、 tool 関数を 直接 invoke。
core selftest (test_skeleton.py) と 相補 = MCP wrapper が invariant を 保つか verify。
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

from rei_verify.mcp import (
    tool_create_audit_chain,
    tool_append_audit_entry,
    tool_verify_audit_chain,
    tool_record_verdict,
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
# Test group M1: create_audit_chain
# ---------------------------------------------------------------------------

print("\n[M1] create_audit_chain")

with tempfile.TemporaryDirectory() as tdir:
    _reset_state_for_test()

    # invalid name
    r = tool_create_audit_chain("bad name with spaces", base_dir=tdir)
    check("error" in r, f"invalid name rejected (got {r})")

    r = tool_create_audit_chain("./../../etc", base_dir=tdir)
    check("error" in r, f"path-injection name rejected (got {r})")

    # valid create
    r = tool_create_audit_chain("test-chain_01", base_dir=tdir)
    check("chain_id" in r and "error" not in r, f"valid name accepted (got {r})")
    check(r["existing_entries"] == 0, f"new chain has 0 existing entries")
    check(r["path"].endswith("test-chain_01.jsonl"), f"path derived from name")

    chain_id_1 = r["chain_id"]

    # second chain
    r2 = tool_create_audit_chain("other-chain", base_dir=tdir)
    check(r2["chain_id"] != chain_id_1, "distinct chain_ids")


# ---------------------------------------------------------------------------
# Test group M2: append_audit_entry
# ---------------------------------------------------------------------------

print("\n[M2] append_audit_entry")

with tempfile.TemporaryDirectory() as tdir:
    _reset_state_for_test()

    r = tool_append_audit_entry("nonexistent-chain-id", {"x": 1})
    check("error" in r, f"append to missing chain rejected (got {r})")

    r = tool_create_audit_chain("m2", base_dir=tdir)
    cid = r["chain_id"]

    # non-dict entry
    r = tool_append_audit_entry(cid, "not a dict")  # type: ignore[arg-type]
    check("error" in r, f"non-dict entry rejected (got {r})")

    # valid append
    r1 = tool_append_audit_entry(cid, {"phase": "step1", "n": 1})
    r2 = tool_append_audit_entry(cid, {"phase": "step2", "n": 2})
    check(r1["hash"] != r2["hash"], "distinct hashes")
    check(r1["seq"] == 0 and r2["seq"] == 1, f"seq increments (got {r1['seq']}, {r2['seq']})")


# ---------------------------------------------------------------------------
# Test group M3: verify_audit_chain (tamper detection through MCP)
# ---------------------------------------------------------------------------

print("\n[M3] verify_audit_chain")

with tempfile.TemporaryDirectory() as tdir:
    _reset_state_for_test()

    r = tool_create_audit_chain("m3", base_dir=tdir)
    cid = r["chain_id"]
    chain_path = Path(r["path"])

    tool_append_audit_entry(cid, {"phase": "A"})
    tool_append_audit_entry(cid, {"phase": "B"})
    tool_append_audit_entry(cid, {"phase": "C"})

    v = tool_verify_audit_chain(cid)
    check(v["ok"] and v["entry_count"] == 3 and v["broken_at"] is None,
          f"clean chain verify OK (got {v})")

    # tamper
    lines = chain_path.read_text(encoding="utf-8").splitlines()
    import json as _json
    obj = _json.loads(lines[1])
    obj["entry"] = {"phase": "TAMPERED"}
    lines[1] = _json.dumps(obj, ensure_ascii=False, sort_keys=True)
    chain_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    v = tool_verify_audit_chain(cid)
    check(not v["ok"], f"tamper detected (got ok={v['ok']})")
    check(v["broken_at"] == 1, f"broken_at correct (got {v['broken_at']})")

    # verify on missing chain
    v = tool_verify_audit_chain("no-such-chain")
    check("error" in v, "missing chain rejected")


# ---------------------------------------------------------------------------
# Test group M4: record_verdict — validation + invariant
# ---------------------------------------------------------------------------

print("\n[M4] record_verdict validation")

with tempfile.TemporaryDirectory() as tdir:
    _reset_state_for_test()

    r = tool_create_audit_chain("m4", base_dir=tdir)
    cid = r["chain_id"]

    # empty claim
    r = tool_record_verdict(cid, "", "confirmed")
    check("error" in r, "empty claim rejected")

    # invalid verdict
    r = tool_record_verdict(cid, "some claim", "maybe")
    check("error" in r, f"invalid verdict rejected (got {r})")

    # missing chain
    r = tool_record_verdict("missing-chain", "x", "confirmed")
    check("error" in r, "missing chain rejected")

    # CONFIRMED without markers = OK
    r = tool_record_verdict(cid, "1+1==2", "confirmed")
    check("error" not in r and r["verdict"] == "confirmed",
          f"CONFIRMED without markers accepted (got {r})")
    check(r["dfumt"] == "TRUE", f"CONFIRMED maps to dfumt TRUE (got {r.get('dfumt')})")

    # HOLDING without markers = ERROR (「沈黙を成功と偽装しない」)
    r = tool_record_verdict(cid, "big claim", "holding", markers=[])
    check("error" in r and "沈黙" in r["error"],
          f"HOLDING without markers rejected with 沈黙 error (got {r})")

    # REFUTED without markers = ERROR
    r = tool_record_verdict(cid, "big claim", "refuted", markers=[])
    check("error" in r, "REFUTED without markers rejected")

    # INCOMPLETE_FRAME without markers = ERROR
    r = tool_record_verdict(cid, "big claim", "incomplete_frame", markers=[])
    check("error" in r, "INCOMPLETE_FRAME without markers rejected")

    # HOLDING with valid markers = OK
    r = tool_record_verdict(
        cid, "n^2 > 100 for all n >= 11", "holding",
        markers=[{
            "dimension": "search_space",
            "what_was_tried": "n in [11, 1000]",
            "what_was_not_tried": "n > 1000",
            "reason": "budget cutoff",
        }],
    )
    check("error" not in r and r["verdict"] == "holding" and r["marker_count"] == 1,
          f"HOLDING with markers OK (got {r})")
    check(r["dfumt"] == "NEITHER", f"HOLDING maps to dfumt NEITHER (got {r.get('dfumt')})")

    # invalid dimension in marker
    r = tool_record_verdict(
        cid, "x", "holding",
        markers=[{
            "dimension": "invalid_dimension",
            "what_was_tried": "a",
            "what_was_not_tried": "b",
            "reason": "c",
        }],
    )
    check("error" in r and "invalid" in r["error"].lower(),
          f"invalid dimension rejected (got {r})")

    # missing marker field
    r = tool_record_verdict(
        cid, "x", "refuted",
        markers=[{"dimension": "witness_type", "what_was_tried": "y"}],  # missing 2 fields
    )
    check("error" in r, f"marker with missing fields rejected (got {r})")

    # REFUTED with valid witness marker
    r = tool_record_verdict(
        cid, "1+1==3", "refuted",
        markers=[{
            "dimension": "witness_type",
            "what_was_tried": "arithmetic evaluation",
            "what_was_not_tried": "reinterpretation of == operator",
            "reason": "1+1 evaluates to 2, not 3, in standard arithmetic",
        }],
    )
    check("error" not in r and r["verdict"] == "refuted",
          f"REFUTED with witness OK (got {r})")
    check(r["dfumt"] == "FALSE", f"REFUTED maps to dfumt FALSE (got {r.get('dfumt')})")


# ---------------------------------------------------------------------------
# Test group M5: chain integrity after record_verdict
# ---------------------------------------------------------------------------

print("\n[M5] chain integrity after mixed operations")

with tempfile.TemporaryDirectory() as tdir:
    _reset_state_for_test()

    r = tool_create_audit_chain("m5", base_dir=tdir)
    cid = r["chain_id"]

    tool_append_audit_entry(cid, {"phase": "setup"})
    tool_record_verdict(cid, "claim A", "confirmed")
    tool_record_verdict(cid, "claim B", "holding", markers=[{
        "dimension": "compute_budget", "what_was_tried": "10s",
        "what_was_not_tried": ">10s", "reason": "timeout",
    }])
    tool_append_audit_entry(cid, {"phase": "cleanup"})

    v = tool_verify_audit_chain(cid)
    check(v["ok"] and v["entry_count"] == 4,
          f"mixed chain integrity ok (got {v})")


# ---------------------------------------------------------------------------
# Test group M6: MCP registration smoke (no actual serve)
# ---------------------------------------------------------------------------

print("\n[M6] _register_mcp smoke test")

from rei_verify.mcp import _register_mcp
server = _register_mcp()
check(server is not None, "MCP server registration returns non-None (mcp package available)")


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

print(f"\n{'=' * 60}")
print(f"MCP layer result: {passed} passed / {failed} failed")
print(f"{'=' * 60}")
sys.exit(0 if failed == 0 else 1)
