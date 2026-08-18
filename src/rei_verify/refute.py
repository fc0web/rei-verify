"""
rei-verify refute.

`refute_lean_source(claim, lean_source, ...)`:
  Lean 4 source を 実行し、 指定 theorem が
    - 「許可 axiom のみ」 で 証明されているか
    - sorry / native_decide の 依存なし か
  を verify、 VerdictWithMarkers を 返す (4 値 verdict + 型的 marker invariant)。

Verdict rule ([[feedback-zero-sorry-floor-not-ceiling]] discipline 型化):
  - lean bin 不在 / source 空 / theorem 名 空 / theorem 名 が source に 無い
      → INCOMPLETE_FRAME
  - lean 実行 exit != 0                     → REFUTED (build error を witness)
  - "sorryAx" が axiom 依存に あり            → HOLDING (sorry marker)
  - allow_axioms 外の axiom 依存 あり         → HOLDING (disallowed axiom marker)
  - "Lean.ofReduceBool" (native_decide 起点)  → HOLDING (kernel out marker)
  - 上記 全て clean                          → CONFIRMED

Default allow_axioms = Mathlib base [propext, Classical.choice, Quot.sound]。
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .core import (
    Verdict,
    IncompleteMarker,
    PostCheckResult,
    VerdictWithMarkers,
    VerifiedExecution,
)
from .audit import AuditChain


# Mathlib base axioms (Rei's [[feedback-zero-sorry-floor-not-ceiling]] default)
DEFAULT_ALLOWED_AXIOMS: frozenset[str] = frozenset({
    "propext",
    "Classical.choice",
    "Quot.sound",
})


# ---------------------------------------------------------------------------
# Public helpers (independently testable = mock 不要)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AxiomParseResult:
    """`#print axioms X` の出力から抽出した結果。

    axioms=None → parse 失敗 (output に該当行なし = HOLDING frame marker 対象)
    axioms=set() → 「does not depend on any axioms」 (完全 axiom-free)
    axioms={...} → axiom list
    """
    axioms: frozenset[str] | None
    raw_line: str


def parse_lean_axioms(stdout: str, theorem_name: str) -> AxiomParseResult:
    """`#print axioms {theorem_name}` の出力を parse。

    Lean 4 の出力パターン (2 種):
      1. `'X' depends on axioms: [propext, Classical.choice, Quot.sound]`
      2. `'X' does not depend on any axioms`
    """
    # single-quote or backtick quoting, robust
    tn_escaped = re.escape(theorem_name)

    # pattern 2 (no dependencies) — 先に check
    no_axiom_pat = re.compile(
        rf"['`]{tn_escaped}['`]\s+does\s+not\s+depend\s+on\s+any\s+axioms",
        re.IGNORECASE,
    )
    m2 = no_axiom_pat.search(stdout)
    if m2:
        return AxiomParseResult(axioms=frozenset(), raw_line=m2.group(0))

    # pattern 1 (axiom list) — multiline 対応 (Lean 4 は 長 list を改行 wrap)
    depends_pat = re.compile(
        rf"['`]{tn_escaped}['`]\s+depends\s+on\s+axioms:\s*\[([^\]]*)\]",
        re.IGNORECASE | re.DOTALL,
    )
    m1 = depends_pat.search(stdout)
    if m1:
        raw = m1.group(1)
        # comma-separated, 改行含む
        axioms = {a.strip() for a in raw.split(",") if a.strip()}
        return AxiomParseResult(axioms=frozenset(axioms), raw_line=m1.group(0))

    return AxiomParseResult(axioms=None, raw_line="")


def classify_axioms(
    axioms: frozenset[str],
    allowed: frozenset[str] = DEFAULT_ALLOWED_AXIOMS,
) -> list[IncompleteMarker]:
    """axiom set から non-CONFIRMED markers を 生成。 empty return = CONFIRMED 相当。"""
    markers: list[IncompleteMarker] = []

    # sorry check (highest priority signal)
    if "sorryAx" in axioms or "sorry" in axioms:
        markers.append(IncompleteMarker(
            dimension="witness_type",
            what_was_tried="axiom check post-build",
            what_was_not_tried="sorry-free proof",
            reason=f"axioms include sorryAx: {sorted(axioms)}",
        ))

    # native_decide dependency (kernel-out reduction)
    if "Lean.ofReduceBool" in axioms or "Lean.ofReduceNat" in axioms:
        markers.append(IncompleteMarker(
            dimension="witness_type",
            what_was_tried="axiom check post-build",
            what_was_not_tried="kernel-verified proof",
            reason=(
                "Lean.ofReduceBool/Nat present → native_decide 系 = "
                "verification は Lean kernel 外の runtime 依存"
            ),
        ))

    # disallowed axioms (excluding sorry/native_decide already flagged)
    already_flagged = {"sorryAx", "sorry", "Lean.ofReduceBool", "Lean.ofReduceNat"}
    disallowed = (axioms - allowed) - already_flagged
    if disallowed:
        markers.append(IncompleteMarker(
            dimension="witness_type",
            what_was_tried=f"axiom check against allow_axioms={sorted(allowed)}",
            what_was_not_tried="reducing to allowed axioms only",
            reason=f"disallowed user axioms present: {sorted(disallowed)}",
        ))

    return markers


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def refute_lean_source(
    claim: str,
    lean_source: str,
    audit: AuditChain,
    theorem_name: str,
    allow_axioms: list[str] | None = None,
    timeout_sec: int = 120,
    lean_bin: str | None = None,
) -> VerdictWithMarkers:
    """Lean 4 source を 実行、 theorem_name が 許可 axiom のみで 証明されているか verify。

    Returns:
      VerdictWithMarkers (4 値 verdict + markers + audit hashes)。

    要件:
      - `lean` binary が PATH に あるか lean_bin で 明示指定
      - lean_source 内に `theorem_name` の 定義が 存在 (rough grep check)
      - Mathlib 依存の proof の 場合は 呼び出し側で lake project 経由必要
        (本 skeleton は 単一 file 実行のみ、 stdlib のみ 証明対象)
    """
    allowed = frozenset(allow_axioms) if allow_axioms is not None else DEFAULT_ALLOWED_AXIOMS
    lean_exe = lean_bin or shutil.which("lean")

    def pre_check() -> bool:
        # source 空
        if not lean_source or not lean_source.strip():
            return False
        # theorem 名 空
        if not theorem_name or not theorem_name.strip():
            return False
        # lean bin 未 install
        if lean_exe is None:
            return False
        if not Path(lean_exe).exists():
            return False
        # theorem 名 が source に 含まれない (rough check、 comment 内も match するが 過小 reject より 過大 accept)
        if theorem_name not in lean_source:
            return False
        return True

    def _augment_source(src: str) -> str:
        """`#print axioms {theorem_name}` を末尾に追加 (未 present なら)。"""
        if f"#print axioms {theorem_name}" in src:
            return src
        return src.rstrip() + f"\n\n#print axioms {theorem_name}\n"

    def action() -> dict:
        combined = _augment_source(lean_source)
        # temp file 経由で subprocess (in-memory pipe より reliable、 lean は file path 要)
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lean", delete=False, encoding="utf-8",
        ) as tf:
            tf.write(combined)
            src_path = Path(tf.name)
        try:
            r = subprocess.run(
                [lean_exe, str(src_path)],
                capture_output=True,
                timeout=timeout_sec,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return {
                "returncode": r.returncode,
                "stdout": r.stdout or "",
                "stderr": r.stderr or "",
            }
        finally:
            try:
                src_path.unlink()
            except OSError:
                pass

    def post_check(result: dict) -> PostCheckResult:
        rc = result["returncode"]
        stdout = result["stdout"]
        stderr = result["stderr"]

        # build fail = REFUTED (Lean が 拒否 = 型的 counter-witness)
        if rc != 0:
            snippet = (stderr.strip() or stdout.strip())[:800]
            witness = IncompleteMarker(
                dimension="witness_type",
                what_was_tried="lean 4 type check + build",
                what_was_not_tried="successful compilation",
                reason=f"lean exit={rc}: {snippet}",
            )
            return PostCheckResult(refuted=True, markers=[witness])

        # build succeeded → parse axioms output
        parsed = parse_lean_axioms(stdout, theorem_name)
        if parsed.axioms is None:
            # output に `#print axioms` 該当行なし = HOLDING (parser frame gap)
            marker = IncompleteMarker(
                dimension="frame",
                what_was_tried=f"parse '#print axioms {theorem_name}' output",
                what_was_not_tried="verdict determination (axiom list not found in stdout)",
                reason=(
                    f"stdout did not contain expected pattern; "
                    f"stdout[:500]={stdout[:500]!r}"
                ),
            )
            return PostCheckResult(refuted=False, markers=[marker])

        # axioms parsed → classify
        markers = classify_axioms(parsed.axioms, allowed)
        return PostCheckResult(refuted=False, markers=markers)

    ve = VerifiedExecution(
        claim=claim,
        pre_check=pre_check,
        post_check=post_check,
        audit=audit,
    )
    return ve.run(action)
