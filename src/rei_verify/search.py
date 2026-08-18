"""
rei-verify search.

`search_counterexample(claim, predicate, space, ...)`:
  iterable space の 各 sample に predicate を 適用、 counter-example を 探す。
  見つかったら REFUTED、 見つからず budget 尽きたら HOLDING (「見つからなかった
  探索空間の形」 marker 添付)、 pre-check fail は INCOMPLETE_FRAME。

Verdict rule:
  - predicate not callable / space None / iter(space) 失敗
      → INCOMPLETE_FRAME
  - action raises (predicate 全体で catastrophic 失敗)
      → REFUTED (default exception classifier)
  - counter-example 発見              → REFUTED (witness marker)
  - space 全 iterate 完 (finite exhaustion)
      → HOLDING (search_space marker、 「exhaustion of finite sample ≠ 一般命題正しさ」)
  - max_time_sec 到達               → HOLDING (compute_budget marker)
  - max_samples 到達                → HOLDING (compute_budget marker)

★ 意図的 non-CONFIRMED: 「absence of counter-example is not proof」 discipline。
  bounded claim (「for all n in [1, 100]」) に対して exhaustion で確定させたい
  場合は、 caller が 別途 record_verdict("confirmed") を 記録すべき。
  search_counterexample tool 自体は 常に REFUTED か HOLDING を 返す。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Iterable

from .core import (
    Verdict,
    IncompleteMarker,
    PostCheckResult,
    VerdictWithMarkers,
    VerifiedExecution,
)
from .audit import AuditChain


# ---------------------------------------------------------------------------
# Python API (full callable predicate + iterable space)
# ---------------------------------------------------------------------------

def search_counterexample(
    claim: str,
    predicate: Callable[[Any], bool],
    space: Iterable[Any],
    audit: AuditChain,
    max_samples: int = 10_000,
    max_time_sec: float = 30.0,
    space_description: str = "",
    sample_repr: Callable[[Any], str] | None = None,
) -> VerdictWithMarkers:
    """iterable space の 各 sample に predicate を 適用、 counter-example 探索。

    Args:
      claim: 検証対象の 主張 (自然言語)。
      predicate: sample → bool。 True で 「その sample が claim を 反証する」 semantics。
      space: iterable (list / range / generator OK)。 空 も OK (即 HOLDING return)。
      audit: audit chain (verdict + phase entry 記録先)。
      max_samples: 打ち切り sample 数 (budget)。
      max_time_sec: 打ち切り wall-clock 秒 (budget)。
      space_description: space の 人間可読 記述 (marker に埋め込み)。
      sample_repr: counter-example の str 表現 (default repr())。

    Returns:
      VerdictWithMarkers。 CONFIRMED は 返さない (「absence ≠ proof」 discipline)。
    """
    sample_repr_fn = sample_repr if sample_repr is not None else repr
    desc = space_description or "user-provided space"

    def pre_check() -> bool:
        if not callable(predicate):
            return False
        if space is None:
            return False
        try:
            iter(space)
        except TypeError:
            return False
        if max_samples <= 0:
            return False
        if max_time_sec <= 0:
            return False
        return True

    def action() -> dict:
        samples_tested = 0
        predicate_errors = 0
        start = time.perf_counter()
        witnesses: list[Any] = []

        try:
            for sample in space:
                # budget checks 先に (loop 開始時点)
                if samples_tested >= max_samples:
                    exit_reason = "sample_limit"
                    break
                elapsed = time.perf_counter() - start
                if elapsed >= max_time_sec:
                    exit_reason = "time_limit"
                    break

                samples_tested += 1
                try:
                    if predicate(sample):
                        witnesses.append(sample)
                        exit_reason = "witness"
                        break
                except BaseException:
                    # per-sample predicate error は skip (search 続行)、 集計は 別途
                    predicate_errors += 1
                    continue
            else:
                # for-else: for が break 無しで 完走 → space 全 exhaust
                exit_reason = "exhausted"
        except BaseException:
            # space iteration 自体が raise (predicate ではなく generator error)
            # = catastrophic frame issue、 上位 VerifiedExecution の
            # exception_classifier に 委ねる
            raise

        elapsed_final = time.perf_counter() - start
        return {
            "samples_tested": samples_tested,
            "predicate_errors": predicate_errors,
            "witnesses": [sample_repr_fn(w) for w in witnesses],
            "elapsed_sec": elapsed_final,
            "exit_reason": exit_reason,
        }

    def post_check(r: dict) -> PostCheckResult:
        reason = r["exit_reason"]
        samples = r["samples_tested"]
        elapsed = r["elapsed_sec"]
        preds_err = r["predicate_errors"]

        # (1) counter-example 発見 → REFUTED
        if reason == "witness":
            witness_str = r["witnesses"][0]
            witness_marker = IncompleteMarker(
                dimension="witness_type",
                what_was_tried=f"predicate on {samples} samples from {desc}",
                what_was_not_tried="further samples (counter-example already found)",
                reason=f"counter-example witness: {witness_str}",
            )
            markers = [witness_marker]
            if preds_err > 0:
                markers.append(IncompleteMarker(
                    dimension="frame",
                    what_was_tried="predicate on all samples",
                    what_was_not_tried=f"{preds_err} samples where predicate raised",
                    reason=f"{preds_err} samples raised in predicate (skipped)",
                ))
            return PostCheckResult(refuted=True, markers=markers)

        # (2-4) counter-example なし → HOLDING (never CONFIRMED per discipline)
        markers: list[IncompleteMarker] = []
        if reason == "exhausted":
            markers.append(IncompleteMarker(
                dimension="search_space",
                what_was_tried=(
                    f"exhaustive iteration over {desc} ({samples} samples, {elapsed:.2f}s)"
                ),
                what_was_not_tried="claim extension beyond this finite space",
                reason=(
                    f"finite space fully iterated with no counter-example found; "
                    "exhaustion of finite sample does NOT confirm general claim "
                    "(caller may record_verdict('confirmed') separately if claim is bounded)"
                ),
            ))
        elif reason == "time_limit":
            markers.append(IncompleteMarker(
                dimension="compute_budget",
                what_was_tried=f"predicate on {samples} samples from {desc} ({elapsed:.2f}s)",
                what_was_not_tried="further samples (wall-clock budget hit)",
                reason=f"max_time_sec={max_time_sec} reached at {elapsed:.2f}s",
            ))
        elif reason == "sample_limit":
            markers.append(IncompleteMarker(
                dimension="compute_budget",
                what_was_tried=f"{samples} samples from {desc} ({elapsed:.2f}s)",
                what_was_not_tried="further samples (sample count budget hit)",
                reason=f"max_samples={max_samples} reached",
            ))
        else:
            # sanity fallback (should not reach)
            markers.append(IncompleteMarker(
                dimension="frame",
                what_was_tried=f"{samples} samples from {desc}",
                what_was_not_tried="verdict determination (unknown exit path)",
                reason=f"loop exited with unexpected reason {reason!r}",
            ))

        if preds_err > 0:
            markers.append(IncompleteMarker(
                dimension="frame",
                what_was_tried="predicate on all iterated samples",
                what_was_not_tried=f"{preds_err} samples where predicate raised",
                reason=(
                    f"{preds_err} samples raised in predicate (skipped、 counter-example "
                    "may have existed among skipped samples)"
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


# ---------------------------------------------------------------------------
# Sandboxed predicate expression (MCP layer 用)
# ---------------------------------------------------------------------------

# Safe builtins whitelist for restricted eval.
# ★ 意図的 minimal set (__import__ / exec / eval / open / getattr 系 全 排除)。
# Predicate expression は 数値 or boolean 判定用途に限定。
_SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "sum": sum, "len": len,
    "int": int, "float": float, "str": str, "bool": bool, "round": round,
    "any": any, "all": all,
    "range": range,
    "True": True, "False": False, "None": None,
}


class RestrictedExpressionError(ValueError):
    """restricted expression の 構文 or eval error。 caller に 直接返す。"""


def compile_predicate_expression(
    expr: str,
    var_name: str = "x",
) -> Callable[[Any], bool]:
    """`var_name` variable を 受け取る expression string を callable に compile。

    使い方例:
      p = compile_predicate_expression("x % 2 == 0 and x > 100")
      p(150)  # True

      # breakpoint 用途では var_name="ctx" で dict binding 想定
      p = compile_predicate_expression("ctx['n'] > 0", var_name="ctx")
      p({"n": 5})  # True

    許可された builtins は _SAFE_BUILTINS のみ (import / attribute access は blocked)。
    """
    if not isinstance(expr, str) or not expr.strip():
        raise RestrictedExpressionError("predicate expression must be non-empty string")
    if not var_name or not var_name.isidentifier():
        raise RestrictedExpressionError(
            f"var_name must be valid Python identifier, got {var_name!r}"
        )

    # 危険 substring の 事前拒否 (完全防御ではないが 第 1 段 filter)
    forbidden = ["__", "import", "exec(", "eval(", "open(", "compile(", "globals(", "locals("]
    lower = expr.lower()
    for tok in forbidden:
        if tok in lower:
            raise RestrictedExpressionError(
                f"forbidden token in expression: {tok!r} (restricted eval)"
            )

    try:
        code = compile(expr, "<predicate>", "eval")
    except SyntaxError as e:
        raise RestrictedExpressionError(f"predicate SyntaxError: {e}")

    def predicate(value: Any) -> bool:
        try:
            result = eval(code, {"__builtins__": _SAFE_BUILTINS}, {var_name: value})
        except BaseException:
            # per-sample error は caller (search_counterexample / assert_breakpoints)
            # 側で 集計、 raise を そのまま 上げる
            raise
        return bool(result)

    return predicate
