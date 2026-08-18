"""
rei-verify core primitives.

Verdict / IncompleteMarker / PostCheckResult / VerifiedExecution。

沈黙を 成功と 偽装しない = CONFIRMED 以外の verdict は 必ず marker を 伴う。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

from .audit import AuditChain


# ---------------------------------------------------------------------------
# Verdict (4 値、 D-FUMT₈ subset)
# ---------------------------------------------------------------------------

class Verdict(str, Enum):
    """反証機械の 出力 verdict (4 値)。

    Design (see DESIGN.md § 4 primitives / #1):
      - binary TRUE/FALSE にしない ← 反証されなかった ≠ 正しい
      - HOLDING を CONFIRMED に 自動昇格しない ← IUT 12 年 discipline
      - INCOMPLETE_FRAME を REFUTED と区別 ← 主張自体が well-formed でない
    """

    CONFIRMED = "confirmed"
    """post-condition PASS + incomplete_markers 空。 [[feedback-zero-sorry-floor]] の 最も厳格特殊ケース。"""

    REFUTED = "refuted"
    """具体的な counter-witness (反例 or 破綻証拠) が 得られた。"""

    HOLDING = "holding"
    """counter-witness 未発見 かつ incomplete_markers 非空。 「探索が 尽きた が 反証できていない」。"""

    INCOMPLETE_FRAME = "incomplete_frame"
    """主張自体が well-formed でない (pre-check fail)。 「反証したくても 対象になっていない」。"""


# ---------------------------------------------------------------------------
# IncompleteMarker (「試されずに 残ったもの」 の 型)
# ---------------------------------------------------------------------------

_ALLOWED_DIMENSIONS = frozenset({
    "search_space",     # 探索空間の 未走査部分 (最大の 直接抽象化)
    "witness_type",     # counter-witness の 種類 (numeric / structural / temporal 等) の 未試行分
    "compute_budget",   # 計算予算 (時間 / メモリ / step 数) の 打ち切り
    "frame",            # 主張の frame 自体が incomplete (definition gap / vagueness)
})


@dataclass(frozen=True)
class IncompleteMarker:
    """「見つからなかった探索空間の形」 の 明示化。

    Invariant (VerifiedExecution layer で 強制):
      - Verdict != CONFIRMED の 全 verdict に 1 個以上 必須
      - CONFIRMED に 付けても 良い (透明性向上)

    dimension は 初期 4 種語彙 (search_space / witness_type / compute_budget / frame)。
    それ以外 dimension を 渡すと ValueError を raise。 拡張は operational 経験後に。
    """

    dimension: str
    what_was_tried: str
    what_was_not_tried: str
    reason: str

    def __post_init__(self) -> None:
        if self.dimension not in _ALLOWED_DIMENSIONS:
            raise ValueError(
                f"IncompleteMarker.dimension {self.dimension!r} not in {sorted(_ALLOWED_DIMENSIONS)}. "
                "初期 4 種語彙のみ許可 (拡張は operational 経験後)。"
            )
        if not self.what_was_tried:
            raise ValueError("IncompleteMarker.what_was_tried は 非空 required")
        if not self.what_was_not_tried:
            raise ValueError("IncompleteMarker.what_was_not_tried は 非空 required")
        if not self.reason:
            raise ValueError("IncompleteMarker.reason は 非空 required")

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# PostCheckResult (post_check 関数の 返り値型)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PostCheckResult:
    """post_check の 返り値: (refuted?, counter_witness_or_markers)。

    Rule:
      - refuted=False + markers=[]        → CONFIRMED
      - refuted=True  + witness_marker    → REFUTED
      - refuted=False + markers 非空      → HOLDING
      - refuted=True  + markers=[]        → invalid (REFUTED は witness marker 必須)
    """

    refuted: bool
    markers: list[IncompleteMarker] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.refuted and not self.markers:
            raise ValueError(
                "PostCheckResult(refuted=True) は witness を 1 個以上の marker として 必須。 "
                "「反証した」 と 主張するなら 何が counter-witness か を 型で 提示すべき。"
            )


# ---------------------------------------------------------------------------
# VerdictWithMarkers (VerifiedExecution.run() の 返り値型)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class VerdictWithMarkers:
    """verdict + markers + audit entry hashes。"""

    verdict: Verdict
    markers: list[IncompleteMarker]
    audit_hashes: list[str]   # 本 run で append された audit entry hash 列
    claim: str
    started_at: str           # ISO 8601 UTC
    ended_at: str
    duration_ms: float

    def __post_init__(self) -> None:
        # Invariant: 沈黙を 成功と偽装しない
        if self.verdict != Verdict.CONFIRMED and not self.markers:
            raise ValueError(
                f"Verdict {self.verdict.value!r} は marker を 1 個以上 必須。 "
                "「沈黙を 成功と 偽装しない」 discipline 違反。"
            )

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict.value,
            "markers": [m.to_dict() for m in self.markers],
            "audit_hashes": list(self.audit_hashes),
            "claim": self.claim,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
        }


# ---------------------------------------------------------------------------
# VerifiedExecution
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class VerifiedExecution:
    """pre-check + action + post-check を atomic audit で 束ねる。

    使い方:
        audit = AuditChain(Path("./audit.jsonl"))
        ve = VerifiedExecution(
            claim="Collatz orbit descends within k steps for n=27",
            pre_check=lambda: n == 27 and k > 0,
            post_check=my_post_check,
            audit=audit,
        )
        result = ve.run(lambda: run_collatz_orbit(n=27))
        # result: VerdictWithMarkers

    Verdict 判定 rule (DESIGN.md § 4 primitives / #4 の table):
      pre_check False           → INCOMPLETE_FRAME + frame marker
      action raises             → exception_classifier で 判定 (default: REFUTED with exception witness)
      post PostCheckResult(refuted=False, markers=[]) → CONFIRMED
      post PostCheckResult(refuted=True,  markers=[w])  → REFUTED
      post PostCheckResult(refuted=False, markers=[..])  → HOLDING
    """

    def __init__(
        self,
        claim: str,
        pre_check: Callable[[], bool],
        post_check: Callable[[Any], PostCheckResult],
        audit: AuditChain,
        exception_classifier: Callable[[BaseException], PostCheckResult] | None = None,
    ):
        if not claim:
            raise ValueError("claim は 非空 required (主張が 明示されていない execution は audit 不能)")
        self.claim = claim
        self.pre_check = pre_check
        self.post_check = post_check
        self.audit = audit
        self.exception_classifier = exception_classifier or self._default_exception_classifier

    @staticmethod
    def _default_exception_classifier(exc: BaseException) -> PostCheckResult:
        """action が raise した場合の default 分類。

        default policy: exception 自体を counter-witness として REFUTED 扱い。
        呼び出し側が 別解釈したい場合は exception_classifier を 指定。
        """
        witness = IncompleteMarker(
            dimension="witness_type",
            what_was_tried="action execution",
            what_was_not_tried=f"success path (exception raised: {type(exc).__name__})",
            reason=f"{type(exc).__name__}: {exc}",
        )
        return PostCheckResult(refuted=True, markers=[witness])

    def run(self, action: Callable[[], Any]) -> VerdictWithMarkers:
        started_at = _now_iso()
        start_ms = time.perf_counter()
        audit_hashes: list[str] = []

        # (1) pre-check
        pre_hash = self.audit.append({
            "phase": "pre_check",
            "claim": self.claim,
            "started_at": started_at,
        })
        audit_hashes.append(pre_hash)

        try:
            pre_ok = bool(self.pre_check())
        except BaseException as exc:
            pre_ok = False
            frame_marker = IncompleteMarker(
                dimension="frame",
                what_was_tried="pre_check evaluation",
                what_was_not_tried="action execution (pre_check raised)",
                reason=f"pre_check raised {type(exc).__name__}: {exc}",
            )
            return self._finalize(
                verdict=Verdict.INCOMPLETE_FRAME,
                markers=[frame_marker],
                audit_hashes=audit_hashes,
                started_at=started_at,
                start_ms=start_ms,
                extra_audit={"phase": "pre_check_result", "pre_ok": False, "exception": str(exc)},
            )

        if not pre_ok:
            frame_marker = IncompleteMarker(
                dimension="frame",
                what_was_tried="pre_check evaluation",
                what_was_not_tried="action execution (pre_check returned False)",
                reason="claim frame not satisfied: pre_check returned False",
            )
            return self._finalize(
                verdict=Verdict.INCOMPLETE_FRAME,
                markers=[frame_marker],
                audit_hashes=audit_hashes,
                started_at=started_at,
                start_ms=start_ms,
                extra_audit={"phase": "pre_check_result", "pre_ok": False},
            )

        h = self.audit.append({"phase": "pre_check_result", "pre_ok": True})
        audit_hashes.append(h)

        # (2) action
        h = self.audit.append({"phase": "action_started"})
        audit_hashes.append(h)
        try:
            action_result = action()
        except BaseException as exc:
            post_result = self.exception_classifier(exc)
            h = self.audit.append({
                "phase": "action_raised",
                "exception_type": type(exc).__name__,
                "exception_str": str(exc),
            })
            audit_hashes.append(h)
            return self._finalize_from_post(
                post_result, audit_hashes, started_at, start_ms,
            )

        h = self.audit.append({"phase": "action_ended"})
        audit_hashes.append(h)

        # (3) post-check
        try:
            post_result = self.post_check(action_result)
        except BaseException as exc:
            # post_check 自体が raise = HOLDING (post_check が incomplete)
            marker = IncompleteMarker(
                dimension="frame",
                what_was_tried=f"post_check execution",
                what_was_not_tried=f"verdict determination (post_check raised: {type(exc).__name__})",
                reason=f"post_check raised {type(exc).__name__}: {exc}",
            )
            return self._finalize(
                verdict=Verdict.HOLDING,
                markers=[marker],
                audit_hashes=audit_hashes,
                started_at=started_at,
                start_ms=start_ms,
                extra_audit={"phase": "post_check_raised", "exception": str(exc)},
            )

        return self._finalize_from_post(post_result, audit_hashes, started_at, start_ms)

    def _finalize_from_post(
        self,
        post_result: PostCheckResult,
        audit_hashes: list[str],
        started_at: str,
        start_ms: float,
    ) -> VerdictWithMarkers:
        if post_result.refuted:
            verdict = Verdict.REFUTED
        elif post_result.markers:
            verdict = Verdict.HOLDING
        else:
            verdict = Verdict.CONFIRMED
        return self._finalize(
            verdict=verdict,
            markers=list(post_result.markers),
            audit_hashes=audit_hashes,
            started_at=started_at,
            start_ms=start_ms,
            extra_audit={
                "phase": "post_check_result",
                "refuted": post_result.refuted,
                "marker_count": len(post_result.markers),
            },
        )

    def _finalize(
        self,
        verdict: Verdict,
        markers: list[IncompleteMarker],
        audit_hashes: list[str],
        started_at: str,
        start_ms: float,
        extra_audit: dict,
    ) -> VerdictWithMarkers:
        ended_at = _now_iso()
        duration_ms = (time.perf_counter() - start_ms) * 1000.0

        h = self.audit.append(extra_audit)
        audit_hashes.append(h)

        final_entry = {
            "phase": "verdict",
            "verdict": verdict.value,
            "marker_count": len(markers),
            "ended_at": ended_at,
            "duration_ms": duration_ms,
        }
        h = self.audit.append(final_entry)
        audit_hashes.append(h)

        return VerdictWithMarkers(
            verdict=verdict,
            markers=markers,
            audit_hashes=audit_hashes,
            claim=self.claim,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
        )
