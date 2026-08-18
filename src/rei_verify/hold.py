"""
rei-verify hold.

`hold_verdict(claim, markers, ...)`:
  宣言的 HOLDING generator。 caller が 「試したこと / 試していないこと / 保留理由」
  を IncompleteMarker として明示的に articulate する tool。

他 3 tool との 違い:
  - refute_lean / search_counterexample / assert_breakpoints は 「実行 (execute)」 して
    verdict を 決定
  - hold_verdict は 「宣言 (declare)」 の 型化 — 手作業 refutation の 到達状態を
    audit trail 付きで 型的に 記録

`record_verdict("holding", ...)` との 違い:
  - record_verdict: raw audit entry の 1 行 追加のみ (audit chain 直接 append)
  - hold_verdict: VerifiedExecution 経由の 完全 phase 記録 + 他 3 tool と 同 shape の
    VerdictWithMarkers return (audit chain に pre_check / action / post_check /
    verdict 4 phase 全 append)

Verdict rule:
  - markers 空 / 型不正 (list でない or IncompleteMarker でない items 含む)
      → INCOMPLETE_FRAME
  - markers 有効 → HOLDING (caller markers verbatim)
  - require_multi_dimension=True 指定 かつ 全 marker が 同 dimension
      → HOLDING + augmentation marker (discipline hook、 single-dimension HOLDING は
      「reasoning state の under-articulation」 の 可能性を 型的に flag)

★ REFUTED / CONFIRMED は 返さない (hold は 「保留の 宣言」 に 特化、 実行を伴う
  判定は 他 3 tool の 役割)。 CONFIRMED を 記録したい caller は record_verdict
  ("confirmed", ...) を 使う。
"""
from __future__ import annotations

from typing import Any

from .core import (
    Verdict,
    IncompleteMarker,
    PostCheckResult,
    VerdictWithMarkers,
    VerifiedExecution,
)
from .audit import AuditChain


def hold_verdict(
    claim: str,
    markers: list[IncompleteMarker],
    audit: AuditChain,
    notes: str = "",
    require_multi_dimension: bool = False,
) -> VerdictWithMarkers:
    """宣言的 HOLDING verdict を audit trail 付きで 生成。

    Args:
      claim: 検証対象の 主張 (自然言語)。
      markers: caller の reasoning state を articulate した IncompleteMarker 列。
               1 個以上 必須 (invariant: HOLDING は marker 必須)。
      audit: audit chain。 4 phase (pre_check / action / post_check / verdict) が 追記される。
      notes: 追加 free-text メモ (audit entry に 記録)。
      require_multi_dimension: True の 場合、 全 marker が 同 dimension なら
                               augmentation marker を 追加 (single-dim HOLDING は
                               under-articulation の 可能性、 discipline hook)。

    Returns:
      VerdictWithMarkers、 verdict は 常に HOLDING or INCOMPLETE_FRAME。
      (REFUTED / CONFIRMED は 返さない = tool の 責務分離)。
    """
    # snapshot to freeze semantics (caller mutation between pre/post を防ぐ)
    frozen_markers: list[IncompleteMarker] = (
        list(markers) if isinstance(markers, list) else []
    )

    def pre_check() -> bool:
        if not isinstance(markers, list):
            return False
        if not markers:
            return False
        for m in markers:
            if not isinstance(m, IncompleteMarker):
                return False
        return True

    def action() -> dict:
        # trivial action = caller の 宣言状態を snapshot して return
        dimensions_seen = sorted({m.dimension for m in frozen_markers})
        return {
            "declared_markers": len(frozen_markers),
            "dimensions_seen": dimensions_seen,
            "notes": notes,
        }

    def post_check(result: dict) -> PostCheckResult:
        markers_out: list[IncompleteMarker] = list(frozen_markers)

        # optional discipline: single-dimension HOLDING に augmentation
        if require_multi_dimension and len(result["dimensions_seen"]) < 2:
            aug = IncompleteMarker(
                dimension="frame",
                what_was_tried=(
                    f"HOLDING declaration with markers only in "
                    f"{result['dimensions_seen']} dimension"
                ),
                what_was_not_tried=(
                    "markers spanning other dimensions "
                    "(search_space / witness_type / compute_budget / frame)"
                ),
                reason=(
                    f"require_multi_dimension=True but only 1 dimension used "
                    f"({result['dimensions_seen']}); single-dimension HOLDING may "
                    "under-articulate the reasoning state (why not confirmed AND why "
                    "not refuted should span at least 2 orthogonal reasons)"
                ),
            )
            markers_out.append(aug)

        return PostCheckResult(refuted=False, markers=markers_out)

    ve = VerifiedExecution(
        claim=claim,
        pre_check=pre_check,
        post_check=post_check,
        audit=audit,
    )
    return ve.run(action)
