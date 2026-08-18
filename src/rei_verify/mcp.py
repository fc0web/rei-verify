"""
rei-verify MCP wrapper.

4 tools を expose:
  - create_audit_chain(name, base_dir="") → {chain_id, path}
  - append_audit_entry(chain_id, entry)   → {hash, seq}
  - verify_audit_chain(chain_id)          → ChainVerification dict
  - record_verdict(chain_id, claim, verdict, markers, notes="") → {verdict, hash, ok}

Design:
  - Thin wrapper = MCP layer は 「構造化 audit + verdict 記録」 のみ。
    実 verification logic (Lean 4 subprocess / counter-example search) は
    別 iteration (refute-1 以降) で thick wrapper として追加。
  - 「沈黙を成功と偽装しない」 invariant は record_verdict で 強制。
    CONFIRMED 以外 verdict で markers=[] の場合、 dict error return
    (raise せず、 呼び出し LLM に structured error を 返す)。
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path
from typing import Any

from .core import Verdict, IncompleteMarker, PostCheckResult, VerdictWithMarkers
from .audit import AuditChain, ChainVerification


# in-memory registry of audit chains by id (session-scoped)
# 永続化は audit file 自体で 保証、 chain_id → path mapping は memory
_CHAINS: dict[str, AuditChain] = {}
_CHAIN_META: dict[str, dict] = {}

_DEFAULT_BASE_DIR = Path(os.environ.get(
    "REI_VERIFY_DATA_DIR",
    Path.home() / ".rei-verify" / "audit",
))


# ---------------------------------------------------------------------------
# tool functions (MCP decorator は _register_mcp 内で 適用、 これらは 単体 test 可能)
# ---------------------------------------------------------------------------

def tool_create_audit_chain(name: str, base_dir: str = "") -> dict[str, Any]:
    """named audit chain を 作成。 file 未存在なら 空 chain (append 時に file 作成)。

    Args:
      name: chain の 論理名 (file name の base)。
      base_dir: file を 置く directory。 未指定なら REI_VERIFY_DATA_DIR 環境変数 or
                Path.home()/.rei-verify/audit。

    Returns:
      {"chain_id": str, "path": str, "name": str, "existing_entries": int}
    """
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        return {"error": f"invalid chain name: {name!r} (alnum + _ + - only)"}

    d = Path(base_dir) if base_dir else _DEFAULT_BASE_DIR
    d.mkdir(parents=True, exist_ok=True)
    file_path = d / f"{name}.jsonl"

    chain_id = f"chain-{uuid.uuid4().hex[:12]}"
    chain = AuditChain(file_path)
    _CHAINS[chain_id] = chain
    _CHAIN_META[chain_id] = {"name": name, "path": str(file_path)}

    return {
        "chain_id": chain_id,
        "path": str(file_path),
        "name": name,
        "existing_entries": chain._entry_count,
    }


def tool_append_audit_entry(chain_id: str, entry: dict) -> dict[str, Any]:
    """chain に raw entry を 追記 (verdict validation なし)。 hash + seq を 返す。"""
    chain = _CHAINS.get(chain_id)
    if chain is None:
        return {"error": f"chain not found: {chain_id!r}. call create_audit_chain first."}
    if not isinstance(entry, dict):
        return {"error": f"entry must be dict, got {type(entry).__name__}"}

    try:
        h = chain.append(entry)
    except (TypeError, ValueError) as e:
        return {"error": f"append failed: {type(e).__name__}: {e}"}

    return {
        "chain_id": chain_id,
        "hash": h,
        "seq": chain._entry_count - 1,
    }


def tool_verify_audit_chain(chain_id: str) -> dict[str, Any]:
    """chain integrity を verify。 tamper detection 済 report を dict で 返す。"""
    chain = _CHAINS.get(chain_id)
    if chain is None:
        return {"error": f"chain not found: {chain_id!r}"}

    v: ChainVerification = chain.verify()
    return {
        "chain_id": chain_id,
        "ok": v.ok,
        "entry_count": v.entry_count,
        "broken_at": v.broken_at,
        "last_hash": v.last_hash,
        "reason": v.reason,
    }


_VERDICT_VALUES = {v.value for v in Verdict}


def tool_record_verdict(
    chain_id: str,
    claim: str,
    verdict: str,
    markers: list[dict] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """structured verdict を chain に記録。 「沈黙を成功と偽装しない」 を 型的保証。

    Args:
      chain_id: 記録先 chain。
      claim: 検証対象の 主張 (自然言語 or 形式命題)。
      verdict: "confirmed" | "refuted" | "holding" | "incomplete_frame" のいずれか。
      markers: list of {dimension, what_was_tried, what_was_not_tried, reason}。
               CONFIRMED 以外 verdict は 1 個以上 必須。
               dimension は "search_space" | "witness_type" | "compute_budget" | "frame" のみ。
      notes: 追加 free-text メモ (任意)。

    Returns:
      success: {"chain_id", "hash", "seq", "verdict", "marker_count"}
      error: {"error": str, "dfumt": "FALSE"} — invariant 違反 or chain 未 exist
    """
    if not claim:
        return {"error": "claim must be non-empty", "dfumt": "FALSE"}
    if verdict not in _VERDICT_VALUES:
        return {
            "error": f"verdict must be one of {sorted(_VERDICT_VALUES)}, got {verdict!r}",
            "dfumt": "FALSE",
        }

    chain = _CHAINS.get(chain_id)
    if chain is None:
        return {"error": f"chain not found: {chain_id!r}", "dfumt": "FALSE"}

    # dict → IncompleteMarker (validation を primitive 型で 発火させる)
    markers = markers or []
    parsed_markers: list[IncompleteMarker] = []
    for i, m in enumerate(markers):
        if not isinstance(m, dict):
            return {
                "error": f"markers[{i}] must be dict with dimension/what_was_tried/what_was_not_tried/reason",
                "dfumt": "FALSE",
            }
        try:
            parsed_markers.append(IncompleteMarker(
                dimension=m.get("dimension", ""),
                what_was_tried=m.get("what_was_tried", ""),
                what_was_not_tried=m.get("what_was_not_tried", ""),
                reason=m.get("reason", ""),
            ))
        except ValueError as e:
            return {"error": f"markers[{i}] invalid: {e}", "dfumt": "FALSE"}

    # 「沈黙を成功と偽装しない」 invariant enforcement
    verdict_enum = Verdict(verdict)
    if verdict_enum != Verdict.CONFIRMED and not parsed_markers:
        return {
            "error": (
                f"verdict {verdict!r} requires >=1 IncompleteMarker "
                "(「沈黙を成功と偽装しない」 discipline invariant)"
            ),
            "dfumt": "FALSE",
        }

    entry = {
        "phase": "verdict",
        "claim": claim,
        "verdict": verdict,
        "markers": [m.to_dict() for m in parsed_markers],
        "notes": notes,
    }
    h = chain.append(entry)
    return {
        "chain_id": chain_id,
        "hash": h,
        "seq": chain._entry_count - 1,
        "verdict": verdict,
        "marker_count": len(parsed_markers),
        "dfumt": "TRUE" if verdict_enum == Verdict.CONFIRMED else (
            "FALSE" if verdict_enum == Verdict.REFUTED else "NEITHER"
        ),
    }


def tool_refute_lean(
    chain_id: str,
    claim: str,
    lean_source: str,
    theorem_name: str,
    allow_axioms: list[str] | None = None,
    timeout_sec: int = 120,
) -> dict[str, Any]:
    """Lean 4 source を 実行、 theorem_name が 許可 axiom のみで 証明されているか verify。

    Verdict rule ([[feedback-zero-sorry-floor]] 型化):
      - lean 不在 / source 空 / theorem 名 空 / theorem 名 が source に無い
          → INCOMPLETE_FRAME
      - lean 実行 exit != 0                     → REFUTED (build error を witness)
      - sorryAx / native_decide / disallowed axiom → HOLDING (dimension marker)
      - 上記 全て clean                          → CONFIRMED

    Returns:
      success: {"verdict", "markers", "audit_hashes", "duration_ms", "dfumt"}
      error: {"error": str, "dfumt": "FALSE"}
    """
    chain = _CHAINS.get(chain_id)
    if chain is None:
        return {"error": f"chain not found: {chain_id!r}", "dfumt": "FALSE"}
    if not claim or not claim.strip():
        return {"error": "claim must be non-empty", "dfumt": "FALSE"}
    # Note: lean_source / theorem_name empty checks は refute_lean_source 側の
    # pre_check で INCOMPLETE_FRAME に変換される (input validation error にせず、
    # 型的 verdict として 返すのが 反証機械の 一貫性)。

    # deferred import (mcp package layer と refute layer の 分離維持)
    from .refute import refute_lean_source
    from .core import Verdict

    result = refute_lean_source(
        claim=claim,
        lean_source=lean_source,
        audit=chain,
        theorem_name=theorem_name,
        allow_axioms=allow_axioms,
        timeout_sec=timeout_sec,
    )

    dfumt_map = {
        "confirmed": "TRUE",
        "refuted": "FALSE",
        "holding": "NEITHER",
        "incomplete_frame": "ZERO",
    }
    return {
        "verdict": result.verdict.value,
        "markers": [m.to_dict() for m in result.markers],
        "audit_hashes": result.audit_hashes,
        "claim": result.claim,
        "started_at": result.started_at,
        "ended_at": result.ended_at,
        "duration_ms": result.duration_ms,
        "dfumt": dfumt_map.get(result.verdict.value, "UNKNOWN"),
    }


def tool_search_counterexample_explicit(
    chain_id: str,
    claim: str,
    samples: list[Any],
    predicate_expr: str,
    max_samples: int = 10_000,
    max_time_sec: float = 30.0,
    space_description: str = "",
) -> dict[str, Any]:
    """explicit sample list + restricted expression で counter-example 探索。

    MCP 経由での safe entrypoint = predicate は `x` variable のみを 使う 数値/boolean 判定
    expression、 __import__ / exec / eval / open / attribute (`__`) は 事前 reject、
    builtins は _SAFE_BUILTINS whitelist のみ 許可。

    Verdict rule (search_counterexample と 同一):
      - found → REFUTED (witness marker)
      - exhausted → HOLDING (search_space marker、 「absence ≠ proof」)
      - time/sample limit → HOLDING (compute_budget marker)
      - predicate 構文 error / restricted eval violation → error dict return (invariant 到達前)

    Args:
      chain_id: create_audit_chain で 得た id。
      claim: 検証対象の 主張 (自然言語)。
      samples: 検証 sample list (server 側で iterate)。
      predicate_expr: `x` variable を 使う expression、 True で 「その sample が claim を 反証する」。
                      例: `"x % 3 == 0 and x > 100"`
      max_samples: 打ち切り sample 数。
      max_time_sec: 打ち切り wall-clock 秒。
      space_description: 人間可読 記述 (marker に埋め込み)。

    Returns:
      success: {"verdict", "markers", "audit_hashes", "duration_ms", "dfumt"}
      error:   {"error", "dfumt": "FALSE"}
    """
    chain = _CHAINS.get(chain_id)
    if chain is None:
        return {"error": f"chain not found: {chain_id!r}", "dfumt": "FALSE"}
    if not claim or not claim.strip():
        return {"error": "claim must be non-empty", "dfumt": "FALSE"}
    if not isinstance(samples, list):
        return {"error": f"samples must be list, got {type(samples).__name__}", "dfumt": "FALSE"}

    from .search import (
        search_counterexample,
        compile_predicate_expression,
        RestrictedExpressionError,
    )

    # predicate compile (restricted eval violation は 事前 error return)
    try:
        predicate = compile_predicate_expression(predicate_expr)
    except RestrictedExpressionError as e:
        return {"error": f"predicate rejected: {e}", "dfumt": "FALSE"}

    result = search_counterexample(
        claim=claim,
        predicate=predicate,
        space=iter(samples),  # iter で inline generator 化 (list を pass すると exhausted 意味変わる可能性回避)
        audit=chain,
        max_samples=max_samples,
        max_time_sec=max_time_sec,
        space_description=space_description or f"list of {len(samples)} samples",
        sample_repr=repr,
    )

    dfumt_map = {
        "confirmed": "TRUE",
        "refuted": "FALSE",
        "holding": "NEITHER",
        "incomplete_frame": "ZERO",
    }
    return {
        "verdict": result.verdict.value,
        "markers": [m.to_dict() for m in result.markers],
        "audit_hashes": result.audit_hashes,
        "claim": result.claim,
        "started_at": result.started_at,
        "ended_at": result.ended_at,
        "duration_ms": result.duration_ms,
        "dfumt": dfumt_map.get(result.verdict.value, "UNKNOWN"),
    }


def _reset_state_for_test() -> None:
    """test hook: in-memory registry を clear。 file は 触らず。"""
    _CHAINS.clear()
    _CHAIN_META.clear()


# ---------------------------------------------------------------------------
# MCP server registration + main
# ---------------------------------------------------------------------------

def _register_mcp():
    """MCP server 起動 (import は runtime、 selftest は MCP 無依存)。

    Returns None if mcp package unavailable — caller should print instructive error.
    """
    try:
        from mcp.server import MCPServer  # type: ignore
    except ImportError:
        return None

    from . import __version__

    server = MCPServer(
        name="rei-verify",
        version=__version__,
        instructions=(
            "反証機械の 心臓部を 支える 4 primitives の MCP wrapper。 "
            "hash-chained append-only audit log を 経由して、 4 値 verdict "
            "(CONFIRMED / REFUTED / HOLDING / INCOMPLETE_FRAME) を 型的に 記録する。 "
            "使い方: (1) create_audit_chain で chain 作成 → (2) reasoning の 過程で "
            "append_audit_entry で raw entry を 追記 → (3) 主張ごとに record_verdict "
            "で 4 値 verdict + markers を 記録 → (4) 事後 verify_audit_chain で "
            "tamper detection。 "
            "「沈黙を 成功と 偽装しない」: CONFIRMED 以外の verdict は 必ず "
            "IncompleteMarker (dimension + what_was_tried + what_was_not_tried + "
            "reason) を 1 個以上 添付する 必要がある (type-level invariant)。 "
            "dimension 語彙: search_space / witness_type / compute_budget / frame。"
        ),
    )

    @server.tool()
    def create_audit_chain(name: str, base_dir: str = "") -> dict[str, Any]:
        """named audit chain を 作成する。 chain_id を 返す (以降の tool call で 使う)。

        Args:
          name: chain の 論理名 (alnum + _ + - のみ)。 file base name になる。
          base_dir: 保存先 directory (未指定なら REI_VERIFY_DATA_DIR 環境変数 or ~/.rei-verify/audit)。
        """
        return tool_create_audit_chain(name, base_dir)

    @server.tool()
    def append_audit_entry(chain_id: str, entry: dict) -> dict[str, Any]:
        """chain に raw entry を 追記 (verdict validation なし)。 hash + seq を 返す。

        reasoning の 途中経過を 記録するのに 使う。 verdict 記録は record_verdict を 使うこと。
        """
        return tool_append_audit_entry(chain_id, entry)

    @server.tool()
    def verify_audit_chain(chain_id: str) -> dict[str, Any]:
        """chain integrity を verify。 tamper 検出時は ok=False + broken_at で 位置特定。"""
        return tool_verify_audit_chain(chain_id)

    @server.tool()
    def refute_lean(
        chain_id: str,
        claim: str,
        lean_source: str,
        theorem_name: str,
        allow_axioms: list[str] | None = None,
        timeout_sec: int = 120,
    ) -> dict[str, Any]:
        """Lean 4 source を 実行、 theorem_name が 許可 axiom のみで 証明されているか verify。

        Verdict rule ([[feedback-zero-sorry-floor]] 型化):
          lean 不在 or source/theorem 空 → INCOMPLETE_FRAME
          lean 実行 exit != 0            → REFUTED (build error witness)
          sorryAx / native_decide / disallowed → HOLDING
          上記全て clean                  → CONFIRMED

        Args:
          chain_id: create_audit_chain で 得た id。 verdict + phase entries が chain に記録される。
          claim: 検証対象の 主張 (自然言語 or 形式命題)。
          lean_source: Lean 4 source (単一 file 想定、 Mathlib 依存 は lake project 経由が別 iter)。
          theorem_name: source 内の 対象 theorem 名 (`#print axioms {theorem_name}` を末尾追加)。
          allow_axioms: 許可 axiom list。 default = ["propext", "Classical.choice", "Quot.sound"]。
          timeout_sec: lean subprocess timeout (default 120)。

        Returns:
          {"verdict", "markers", "audit_hashes", "duration_ms", "dfumt"}
        """
        return tool_refute_lean(
            chain_id=chain_id,
            claim=claim,
            lean_source=lean_source,
            theorem_name=theorem_name,
            allow_axioms=allow_axioms,
            timeout_sec=timeout_sec,
        )

    @server.tool()
    def search_counterexample_explicit(
        chain_id: str,
        claim: str,
        samples: list[Any],
        predicate_expr: str,
        max_samples: int = 10_000,
        max_time_sec: float = 30.0,
        space_description: str = "",
    ) -> dict[str, Any]:
        """explicit sample list + restricted expression で counter-example 探索。

        MCP 経由 safe entrypoint = predicate は `x` variable のみ の 数値/boolean 判定
        expression、 __import__ / exec / eval / open / attribute (`__`) は 事前 reject。

        Verdict rule:
          found       → REFUTED (witness marker)
          exhausted   → HOLDING (search_space marker、 「absence ≠ proof」)
          time/sample → HOLDING (compute_budget marker)

        Args:
          chain_id: create_audit_chain で 得た id。
          claim: 検証対象の 主張。
          samples: 検証 sample list。
          predicate_expr: `x` variable を 使う expression。 True で 「その sample が claim を 反証」。
                          例: "x % 3 == 0 and x > 100"
          max_samples: 打ち切り sample 数 (default 10000)。
          max_time_sec: 打ち切り wall-clock 秒 (default 30)。
          space_description: 人間可読 記述 (marker 埋め込み)。
        """
        return tool_search_counterexample_explicit(
            chain_id=chain_id, claim=claim, samples=samples,
            predicate_expr=predicate_expr,
            max_samples=max_samples, max_time_sec=max_time_sec,
            space_description=space_description,
        )

    @server.tool()
    def record_verdict(
        chain_id: str, claim: str, verdict: str,
        markers: list[dict] | None = None, notes: str = "",
    ) -> dict[str, Any]:
        """structured verdict を chain に 記録。 4 値 verdict + marker invariant を 型的保証。

        Args:
          chain_id: 記録先 chain (create_audit_chain で 得た id)。
          claim: 検証対象の 主張。
          verdict: "confirmed" | "refuted" | "holding" | "incomplete_frame"。
          markers: list of {dimension, what_was_tried, what_was_not_tried, reason}。
                   CONFIRMED 以外の verdict は 1 個以上 必須。
                   dimension は "search_space" | "witness_type" | "compute_budget" | "frame" のみ。
          notes: 追加 free-text メモ (任意)。

        「沈黙を 成功と 偽装しない」: markers が 空で verdict != CONFIRMED の場合は error を返す。
        """
        return tool_record_verdict(chain_id, claim, verdict, markers, notes)

    return server


def main() -> int:
    server = _register_mcp()
    if server is None:
        print(
            "rei-verify MCP: 'mcp' package not installed. `pip install mcp` を 実行してください。",
            file=sys.stderr,
        )
        return 1
    server.run()
    return 0
