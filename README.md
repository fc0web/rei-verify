# rei-verify

**反証機械 (refutation machine) の 心臓部を 支える 4 primitives + MCP wrapper**

**Version**: 0.1.0-alpha (2026-08-19) — Skeleton release: 4 primitives (Verdict / IncompleteMarker / AuditChain / VerifiedExecution) + 4 MCP tools。 反証エンジン本体 (refute_lean / search_counterexample / assert_breakpoint / hold_verdict) は次 iteration。

## なぜ 「反証機械」 か

生成 は 飽和する。 refutation は 飽和しない。

Rei stack を通して 育ってきた 4 原則を、 型 に固定した:

1. **`sorry ゼロ` は 「反証されなかった」 の 最も厳格特殊ケース** — sorry / axiom / native_decide を 含む Lean 4 proof は 未検証、 完全 sorry-free proof のみ 「反証されなかった」 を 主張できる
2. **「沈黙を 成功と 偽装しない」** — 探索が 打ち切られたら 「探索空間の どこが 走査されなかったか」 を 明示する
3. **judgmentStatus 4 値** = `confirmed / refuted / holding / incomplete_frame` の 区別 (binary TRUE/FALSE では 反証されなかった を 正しい に 崩壊させる)
4. **hash chain audit** = 全 reasoning 過程を 改竄不能な JSONL log に 記録、 事後の tamper 検証可能

## 4 primitives

### `Verdict` (4 値 enum)

```python
class Verdict(str, Enum):
    CONFIRMED = "confirmed"           # post-condition PASS + marker 空
    REFUTED = "refuted"               # 具体的な counter-witness が 得られた
    HOLDING = "holding"               # counter-witness 未発見 かつ marker 非空
    INCOMPLETE_FRAME = "incomplete_frame"  # 主張自体が well-formed でない
```

Binary TRUE/FALSE に しない = 反証されなかった ≠ 正しい ([[IUT 12 年 holding]] discipline の 型化)。

### `IncompleteMarker`

「見つからなかった探索空間の形」 の 明示化。 `Verdict != CONFIRMED` の 全 verdict に **1 個以上 必須** (type-level invariant)。

```python
@dataclass(frozen=True)
class IncompleteMarker:
    dimension: str        # "search_space" | "witness_type" | "compute_budget" | "frame"
    what_was_tried: str
    what_was_not_tried: str
    reason: str
```

### `AuditChain`

sha256 hash-chained append-only JSONL log。 STEP 1340 rei-automator-mcp AuditLogWriter の 汎用抽出。

```python
chain = AuditChain(Path("audit.jsonl"))
h1 = chain.append({"phase": "test", "n": 1})
v = chain.verify()  # → ChainVerification(ok=True, entry_count=1, ...)
```

Tamper 検出は `verify()` の `broken_at` index で 位置特定可能。

### `VerifiedExecution`

pre-check + action + post-check を **atomic audit** で 束ねる context manager。

```python
ve = VerifiedExecution(
    claim="1 + 1 == 2",
    pre_check=lambda: True,
    post_check=lambda r: PostCheckResult(refuted=(r != 2), markers=[]),
    audit=chain,
)
result: VerdictWithMarkers = ve.run(lambda: 1 + 1)
# result.verdict == Verdict.CONFIRMED
# result.audit_hashes == [h1, h2, h3, h4, h5]  # 5 phase entry
```

Verdict 判定 rule (単一 source of truth):

| pre_check | action | post_check | Verdict |
|---|---|---|---|
| False | (not run) | (not run) | **INCOMPLETE_FRAME** + frame marker |
| True | raises | classify_exception | **REFUTED** (default: exception を counter-witness) |
| True | success | `(False, [])` | **CONFIRMED** |
| True | success | `(True, [w])` | **REFUTED** + witness marker |
| True | success | `(False, [m...])` | **HOLDING** + markers |

## 4 MCP tools

Claude Desktop / Cursor / Cline 等の LLM client から 直接呼べる:

| tool | 役割 |
|---|---|
| `create_audit_chain(name, base_dir="")` | named chain 作成 → `chain_id` return |
| `append_audit_entry(chain_id, entry)` | raw entry 追記 → `{hash, seq}` |
| `verify_audit_chain(chain_id)` | integrity walk → `{ok, entry_count, broken_at, ...}` |
| `record_verdict(chain_id, claim, verdict, markers, notes)` | 4 値 verdict 記録 (marker invariant enforced) |

**「沈黙を 成功と 偽装しない」 の 型的保証**: `record_verdict(..., verdict="holding", markers=[])` は error return する (`「沈黙を成功と偽装しない」 discipline invariant`)。

## Installation

```bash
pip install rei-verify
```

## Usage — library

```python
from rei_verify import Verdict, IncompleteMarker, PostCheckResult, VerifiedExecution, AuditChain
from pathlib import Path

audit = AuditChain(Path("./my-reasoning.jsonl"))

def check_orbit(claim: str, n: int) -> Verdict:
    ve = VerifiedExecution(
        claim=claim,
        pre_check=lambda: n > 0,
        post_check=lambda orbit: PostCheckResult(
            refuted=False,
            markers=[
                IncompleteMarker(
                    dimension="search_space",
                    what_was_tried=f"orbit for n={n}",
                    what_was_not_tried=f"n' > 10^12",
                    reason="budget cutoff",
                )
            ] if len(orbit) > 1000 else [],
        ),
        audit=audit,
    )
    return ve.run(lambda: compute_collatz_orbit(n)).verdict
```

## Usage — MCP (Claude Desktop)

`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "rei-verify": {
      "command": "python",
      "args": ["-m", "rei_verify"]
    }
  }
}
```

## Next iterations (roadmap)

Skeleton は **substrate**。 次 iteration で反証機械本体を composition で追加:

- **refute-1** `refute_lean(claim, lean_source)` — Lean 4 subprocess + sorry count check
- **refute-2** `search_counterexample(claim, space, budget)` — 探索 space 指定 + budget cutoff + marker 生成
- **refute-3** `assert_breakpoint(claim)` — 「その主張が偽なら壊れる場所」 特定
- **refute-4** `hold_verdict(claim, tried, not_tried, reason)` — 明示的 HOLDING 生成

## 累計 test

- **skeleton**: 37 assertion / 全 4 Verdict path + AuditChain hash chain + tamper detection + restart restore
- **MCP layer**: 30 assertion / tool 直接 invoke + invariant enforcement + smoke registration
- **合計 67 assertion / all PASS**

## Design

See `DESIGN.md` for full design rationale (8 節、 4 primitives + verdict rule table + 反証機械 3+1 tool との mapping)。

## Related

- [fc0web/rei-automator-mcp](https://github.com/fc0web/rei-automator-mcp) — Windows PC 自動化 MCP、 AuditChain の 由来 (STEP 1340)
- [fc0web/grounded](https://github.com/fc0web/grounded) — 散文 grounding checker (2-tier verification)
- [fc0web/grounding-check](https://github.com/fc0web/grounding-check) — SCPI hardware grounding checker

## Author

藤本 伸樹 (Nobuki Fujimoto)

## License

MIT (v0.x)。 v1.0+ で AGPL-3.0 + commercial dual への 切替可能性。 LICENSE 参照。

## Honest scope

- (i) skeleton は Lean 4 と 直接連携 しない — `refute_lean` の 中身は 別 iteration
- (ii) `IncompleteMarker.dimension` 語彙は 初期 4 種のみ、 拡張は operational 経験から
- (iii) hash chain は tamper detection 用、 cryptographic signing (Sigstore 等) は 別 concern
- (iv) 「反証機械」 の 新規性 主張なし — property-based testing (Hypothesis) + Lean 4 sorry-check + Coq / Isabelle 系 industry 標準を 統合する discipline layer のみ
