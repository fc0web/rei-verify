#!/usr/bin/env python3
"""
rei-verify integration demo:
  Collatz orbits の t1=1 (trailing_ones=1) class に対する
  Lyapunov α-descent 解析 を assert_breakpoints で 実行。

背景 (藤本さん t1=1 リヤプノフ解析 の 概念):
  区分線形 Lyapunov V に対して 「同時降下条件 V(T(n)) ≤ α · V(n) を
  満たす α < 1 が 存在しない」 という 結果。

本 demo:
  簡略化版 V(n) = log2(n) と 有限 sample 範囲 で、 各 α 候補を
  Breakpoint として assert_breakpoints に投入。 「その α が sample 範囲内 で
  refute される (r(n) > α な witness n が 存在する)」 を assertion とし、
  全 α refute → HOLDING (藤本さん concept 支持) / 未 refute α 存在 → REFUTED
  (探索範囲不足 signal) を 観測。

★ Honest scope (very critical):
  (i) V = log2(n) は 藤本さん 実 V (piecewise linear) の 簡略化
  (ii) descent 条件 V(T(n)) ≤ α·V(n) は 1 種の formalization、 藤本さん とは
       異なる可能性
  (iii) sample [5, ~4M] は 有限、 asymptotic (n→∞) 挙動 は 別議論
  (iv) HOLDING は 「TOOL が 正しく HOLDING を 返した」 の 実証、 藤本さん の
       specific result の 再現 ではない
  (v) 真の 再現は 藤本さん 実 V + 条件 + Lean 4 formalization 経由 (別 iter)
"""
from __future__ import annotations

import math
import sys
from pathlib import Path
from tempfile import mkdtemp

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass

# package を src/ から import
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent / "src"))

from rei_verify import (
    Verdict,
    IncompleteMarker,
    AuditChain,
)
from rei_verify.breakpoint import Breakpoint, assert_breakpoints


# ---------------------------------------------------------------------------
# Collatz dynamics + Lyapunov V
# ---------------------------------------------------------------------------

def collatz_step_odd(n: int) -> int:
    """奇数 n に対する 1 Collatz 「大」 step: T(n) = (3n+1) / 2^ν_2(3n+1)。"""
    m = 3 * n + 1
    while m % 2 == 0:
        m //= 2
    return m


def trailing_ones(n: int) -> int:
    """t1(n) = 奇数 n の 末尾 1-bits 数。 偶数は 0 return。"""
    if n % 2 == 0:
        return 0
    j = 0
    while n & 1:
        j += 1
        n >>= 1
    return j


def V(n: int) -> float:
    """Lyapunov V(n) = log2(n) (簡略化、 piecewise-linear-approximation)。"""
    return math.log2(n) if n > 1 else 0.0


def descent_ratio(n: int) -> float:
    """r(n) = V(T(n)) / V(n) for 奇数 n > 1。 定義域外 は 0.0 return。"""
    if n <= 1:
        return 0.0
    v_n = V(n)
    if v_n == 0.0:
        return 0.0
    return V(collatz_step_odd(n)) / v_n


def t1_ones_class(max_n: int) -> list[int]:
    """odd n ≥ 5, t1(n) = 1 (n ≡ 1 mod 4) を max_n まで 全列挙 (list)。"""
    return [n for n in range(5, max_n + 1, 4) if trailing_ones(n) == 1]


# ---------------------------------------------------------------------------
# Main demo
# ---------------------------------------------------------------------------

def run_demo() -> int:
    print("=" * 70)
    print("rei-verify integration demo: t1=1 Collatz Lyapunov α-descent scan")
    print("via assert_breakpoints (反証機械 3rd tool)")
    print("=" * 70)
    print()

    # -------- 1. sample の 準備 --------
    max_n = 2**22   # ~4M — 単一 process で 数秒
    print(f"[step 1] Enumerating t1=1 class n in [5, {max_n}] (n ≡ 1 mod 4)...")
    samples = t1_ones_class(max_n)
    print(f"         → {len(samples):,} samples")

    # -------- 2. 全 descent ratio を 一度計算 (α-check の 高速化) --------
    print(f"[step 2] Computing descent ratios r(n) = V(T(n)) / V(n) for all samples...")
    sample_ratios = [(n, descent_ratio(n)) for n in samples]
    max_r = max(r for _, r in sample_ratios)
    max_r_n = max(sample_ratios, key=lambda pair: pair[1])[0]
    print(f"         → max r(n) = {max_r:.6f} attained at n = {max_r_n:,}")
    print(f"         → typical r(5) = {descent_ratio(5):.4f}, r(9) = {descent_ratio(9):.4f}, r(17) = {descent_ratio(17):.4f}")

    # -------- 3. α candidates + Breakpoint 構築 --------
    alpha_candidates = [0.5, 0.7, 0.85, 0.9, 0.93, 0.95, 0.97, 0.99]
    print()
    print(f"[step 3] Building {len(alpha_candidates)} Breakpoints (one per α candidate)...")
    print(f"         polarity: assertion(α) = True  ⟺  ∃n in sample: r(n) > α  ⟺  α refuted")
    print(f"         verdict interpretation:")
    print(f"           HOLDING (all α refuted) → supports 藤本さん concept ('∄α<1 satisfies descent')")
    print(f"           REFUTED (some α passes) → sample range insufficient to refute that α")

    def make_assertion(alpha: float, ratios: list[tuple[int, float]]):
        # 探索 = 単一 max 比較 (precomputed max_r と 同義、 但し contextual witness 取得)
        def check() -> bool:
            for n, r in ratios:
                if r > alpha:
                    return True   # α refuted (witness = n)
            return False
        return check

    def first_witness(alpha: float, ratios) -> tuple[int, float] | None:
        for n, r in ratios:
            if r > alpha:
                return (n, r)
        return None

    breakpoints: list[Breakpoint] = []
    for a in alpha_candidates:
        wit = first_witness(a, sample_ratios)
        ctx = {
            "alpha": a,
            "witness_n": wit[0] if wit else None,
            "witness_ratio": round(wit[1], 6) if wit else None,
            "max_ratio_in_sample": round(max_r, 6),
            "sample_count": len(samples),
        }
        breakpoints.append(Breakpoint(
            label=f"α={a}",
            assertion=make_assertion(a, sample_ratios),
            context=ctx,
        ))

    # -------- 4. audit chain + assert_breakpoints 実行 --------
    tmpdir = Path(mkdtemp(prefix="rei-verify-demo-"))
    audit_path = tmpdir / "collatz_t1_ones_lyapunov.jsonl"
    audit = AuditChain(audit_path)
    print()
    print(f"[step 4] Running assert_breakpoints (audit chain: {audit_path})")

    result = assert_breakpoints(
        claim=(
            f"For every α ∈ {alpha_candidates}, "
            f"there exists an odd n in [5, {max_n}] with trailing_ones(n)=1 "
            f"such that V(T(n))/V(n) > α (i.e., α is refuted as descent factor)."
        ),
        breakpoints=breakpoints,
        audit=audit,
        stop_on_first_failure=False,   # 全 α 網羅観測
    )

    # -------- 5. verdict + per-α report --------
    print()
    print("=" * 70)
    print(f"VERDICT: {result.verdict.value.upper()}   (duration {result.duration_ms:.1f} ms)")
    print(f"audit entries: {len(result.audit_hashes)}   markers: {len(result.markers)}")
    print("=" * 70)
    print()

    print("Per-α results (from Breakpoint context):")
    for bp in breakpoints:
        c = bp.context
        if c["witness_n"] is not None:
            print(f"  {bp.label:>7}: WITNESS  n={c['witness_n']:>10,}  r(n)={c['witness_ratio']:.6f}  >  {c['alpha']:.6f}  → α refuted ✓")
        else:
            print(f"  {bp.label:>7}: NO WITNESS in range  max r ≤ {c['max_ratio_in_sample']:.6f}  ≤  {c['alpha']:.6f}  → α NOT refuted in sample")

    print()
    print("Markers (why HOLDING, if applicable):")
    for i, m in enumerate(result.markers, 1):
        print(f"  [{i}] dimension = {m.dimension}")
        print(f"      what_was_tried:     {m.what_was_tried[:120]}")
        print(f"      what_was_not_tried: {m.what_was_not_tried[:120]}")
        print(f"      reason:             {m.reason[:200]}")

    # -------- 6. interpretation --------
    print()
    print("=" * 70)
    print("Interpretation:")
    if result.verdict == Verdict.HOLDING:
        print(f"  All {len(alpha_candidates)} tested α candidates were refuted by concrete t1=1")
        print(f"  witnesses in the sample range [5, {max_n:,}]. Under the simplified V = log2(n),")
        print(f"  no tested α satisfies descent for all t1=1 orbits in this range.")
        print(f"  This SUPPORTS the concept of 藤本さん's Lyapunov result, but is not a proof.")
    elif result.verdict == Verdict.REFUTED:
        # extract failing α from markers (first-failure witness)
        first_fail_bp = next(
            (bp for bp in breakpoints if bp.context["witness_n"] is None),
            None,
        )
        alpha_that_survived = first_fail_bp.context["alpha"] if first_fail_bp else "?"
        print(f"  α = {alpha_that_survived} had NO refuting witness in range [5, {max_n:,}].")
        print(f"  Under V = log2(n), that α satisfies descent for all tested t1=1 n.")
        print(f"  This does NOT disprove 藤本さん's result — it means:")
        print(f"    (a) sample range [5, {max_n:,}] is insufficient to refute that α, OR")
        print(f"    (b) V = log2(n) is different from 藤本さん's actual piecewise linear V.")
        print(f"  Asymptotically r(n) → 1 as n → ∞, so α > lim sup r may indeed be unrefutable")
        print(f"  under this simplified V. This is the tool correctly reporting HOLDING/REFUTED")
        print(f"  based on FINITE evidence — the caller must interpret whether the finite absence")
        print(f"  extends to infinity (a separate mathematical judgment).")
    print()

    print("Honest scope (repeat, cannot be understated):")
    print("  (i) V = log2(n) simplifies 藤本さん's actual piecewise linear V")
    print("  (ii) descent condition V(T(n)) ≤ α·V(n) is one of several formalizations")
    print("  (iii) sample range is finite; asymptotic behavior (n → ∞) not covered by scan")
    print("  (iv) Verdict here demonstrates TOOL correctness, not 藤本さん's specific result")
    print("  (v) True reproduction requires 藤本さん's exact V + condition + Lean 4 formalization")
    print()

    print(f"Audit chain preserved at: {audit_path}")
    print(f"  (verify: python -c \"from rei_verify import AuditChain; import pathlib;")
    print(f"           print(AuditChain(pathlib.Path(r'{audit_path}')).verify())\")")
    print("=" * 70)

    return 0


if __name__ == "__main__":
    sys.exit(run_demo())
