"""
rei-verify breakpoint.

`assert_breakpoints(claim, breakpoints, ...)`:
  各 breakpoint = 「その主張が偽なら 壊れる 具体的な 場所」 を label + assertion
  callable + context で 明示。 assertion False → その breakpoint で claim が
  破綻した witness。 全 pass → 「この N 個の checkpoint では 保たれた が 他は 未 cover」
  の HOLDING。

Verdict rule:
  - breakpoints 空 / 型不正 / assertion 非 callable → INCOMPLETE_FRAME
  - action raises (breakpoints 全体で catastrophic 失敗) → REFUTED (default classifier)
  - 任意 breakpoint の assertion が False → REFUTED (label + context を witness)
  - 全 assertion pass                          → HOLDING (search_space marker)
  - assertion raise は skip + frame marker 併記
  - max_time_sec 到達で 未実行 breakpoint 残った場合 → compute_budget marker 併記

★ 意図的 non-CONFIRMED: search_counterexample と 同 discipline。 「N checkpoints で
  claim が 保たれた」 は 「他の checkpoint でも 保たれる」 ではない。 「これで 全 case
  cover したから CONFIRMED」 と 主張したい場合、 caller が record_verdict("confirmed")
  を 別途記録すべき (case exhaustion の 判断は 人間 責任)。

`search_counterexample` との違い:
  - search_counterexample: 「1 predicate × N values」 の 探索
  - assert_breakpoints: 「N labeled cases × 個別 logic」 の 網羅、 各 case で failure
    が どう manifest するか を label + context で 前もって 特定
  藤本さん t1=1 リヤプノフ解析 (「t1=1, t1=2, ... の 各 case で 個別 α 探索」) の 直接 fit。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .core import (
    Verdict,
    IncompleteMarker,
    PostCheckResult,
    VerdictWithMarkers,
    VerifiedExecution,
)
from .audit import AuditChain


@dataclass(frozen=True)
class Breakpoint:
    """1 個の 検査点。

    label: 人間可読 名前 (「t1=1 case」 「n=0 edge」 等)。 witness / marker に埋め込み。
    assertion: 引数なし callable、 True return で 「この point では claim 保たれた」、
               False return で 「この point で claim 破綻 (この breakpoint が witness)」。
    context: この point の 属性辞書 (座標、 パラメータ、 期待値 等)。 marker に併記される。
    """
    label: str
    assertion: Callable[[], bool]
    context: dict = field(default_factory=dict)


def assert_breakpoints(
    claim: str,
    breakpoints: list[Breakpoint],
    audit: AuditChain,
    stop_on_first_failure: bool = True,
    max_time_sec: float = 60.0,
) -> VerdictWithMarkers:
    """各 breakpoint の assertion を 順次実行。 True/False/raise を 集計して verdict 決定。

    Args:
      claim: 検証対象の 主張 (自然言語)。
      breakpoints: Breakpoint list。 空 は INCOMPLETE_FRAME。
      audit: audit chain。
      stop_on_first_failure: True (default) = 最初の False で break、 False = 全 breakpoint 実行 (集計目的)。
      max_time_sec: 全 breakpoints 合計 の 打ち切り wall-clock 秒。

    Returns:
      VerdictWithMarkers。 CONFIRMED は 返さない (「exhaustion of listed checkpoints ≠ 全 case cover」)。
    """
    def pre_check() -> bool:
        if not isinstance(breakpoints, list):
            return False
        if not breakpoints:
            return False
        for bp in breakpoints:
            if not isinstance(bp, Breakpoint):
                return False
            if not callable(bp.assertion):
                return False
            if not bp.label or not bp.label.strip():
                return False
        if max_time_sec <= 0:
            return False
        return True

    def action() -> dict:
        # results: list of (label, status, detail, context)
        # status: "pass" | "fail" | "error" | "timeout_skip"
        results: list[tuple[str, str, str | None, dict]] = []
        first_failure_index: int | None = None
        error_count = 0
        skip_count = 0
        start = time.perf_counter()

        for i, bp in enumerate(breakpoints):
            elapsed = time.perf_counter() - start
            if elapsed >= max_time_sec:
                skip_count += 1
                results.append((bp.label, "timeout_skip", "wall-clock budget hit before this breakpoint", bp.context))
                continue

            try:
                r = bp.assertion()
                if bool(r):
                    results.append((bp.label, "pass", None, bp.context))
                else:
                    results.append((bp.label, "fail", "assertion returned False", bp.context))
                    if first_failure_index is None:
                        first_failure_index = i
                    if stop_on_first_failure:
                        # mark remaining as timeout_skip for accounting
                        for j in range(i + 1, len(breakpoints)):
                            results.append((
                                breakpoints[j].label,
                                "timeout_skip",
                                "skipped after first failure (stop_on_first_failure=True)",
                                breakpoints[j].context,
                            ))
                            skip_count += 1
                        break
            except BaseException as e:
                error_count += 1
                results.append((bp.label, "error", f"{type(e).__name__}: {e}", bp.context))

        return {
            "results": results,
            "first_failure_index": first_failure_index,
            "error_count": error_count,
            "skip_count": skip_count,
            "elapsed_sec": time.perf_counter() - start,
        }

    def post_check(r: dict) -> PostCheckResult:
        results = r["results"]
        first_fail = r["first_failure_index"]
        error_count = r["error_count"]
        skip_count = r["skip_count"]
        elapsed = r["elapsed_sec"]

        pass_count = sum(1 for res in results if res[1] == "pass")

        # (1) Any assertion False → REFUTED (with first-failure witness)
        if first_fail is not None:
            failed_label, _, detail, ctx = results[first_fail]
            witness = IncompleteMarker(
                dimension="witness_type",
                what_was_tried=f"assertion at breakpoint {failed_label!r}",
                what_was_not_tried=(
                    f"{skip_count} subsequent breakpoints (skipped after first failure)"
                    if stop_on_first_failure else "further assertions in list"
                ),
                reason=f"breakpoint {failed_label!r} assertion returned False; context={ctx!r}; detail={detail}",
            )
            markers = [witness]
            if error_count > 0:
                errored = [res[0] for res in results if res[1] == "error"][:10]
                markers.append(IncompleteMarker(
                    dimension="frame",
                    what_was_tried="all breakpoints attempted",
                    what_was_not_tried=f"{error_count} breakpoints that raised: {errored}",
                    reason=f"{error_count} breakpoints raised in assertion (not counted as failures)",
                ))
            return PostCheckResult(refuted=True, markers=markers)

        # (2) All assertions pass (or mix pass + error, no fail) → HOLDING
        passed_labels = [res[0] for res in results if res[1] == "pass"][:20]
        summary_labels = ", ".join(repr(lbl) for lbl in passed_labels)
        if len(passed_labels) < pass_count:
            summary_labels += f", ... ({pass_count - len(passed_labels)} more)"

        markers: list[IncompleteMarker] = [
            IncompleteMarker(
                dimension="search_space",
                what_was_tried=f"assertions at {pass_count} labeled breakpoints",
                what_was_not_tried="assertions beyond the listed breakpoints (case exhaustion is caller's responsibility)",
                reason=(
                    f"claim held at these {pass_count} checkpoints: {summary_labels[:400]}; "
                    "further checkpoints not enumerated by this run "
                    "(exhaustion of listed checkpoints does NOT confirm general claim)"
                ),
            )
        ]

        if error_count > 0:
            errored_labels = [res[0] for res in results if res[1] == "error"][:20]
            markers.append(IncompleteMarker(
                dimension="frame",
                what_was_tried=f"assertions at {len(results) - skip_count} attempted breakpoints",
                what_was_not_tried=f"assertions at {error_count} breakpoints that raised: {errored_labels}",
                reason=f"{error_count} breakpoints raised in assertion (inconclusive at those points)",
            ))

        if skip_count > 0:
            skipped_labels = [res[0] for res in results if res[1] == "timeout_skip"][:20]
            markers.append(IncompleteMarker(
                dimension="compute_budget",
                what_was_tried=(
                    f"assertions at {pass_count + error_count} breakpoints ({elapsed:.2f}s)"
                ),
                what_was_not_tried=f"assertions at {skip_count} breakpoints skipped (time budget hit): {skipped_labels}",
                reason=(
                    f"max_time_sec={max_time_sec} hit at {elapsed:.2f}s, "
                    f"{skip_count} breakpoints not executed"
                ),
            ))

        return PostCheckResult(refuted=False, markers=markers)

    ve = VerifiedExecution(
        claim=claim,
        pre_check=pre_check,
        post_check=post_check,
        audit=audit,
    )
    return ve.run(action)
