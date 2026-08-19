# rei-verify

**反証機械 (refutation machine)** — 生成ではなく否定を専門にする 検証 infrastructure + MCP server。

**Version**: 0.1.0a1 (2026-08-19) — 4 primitives + 4 refutation tools + 8 MCP tools + integration demo。 test 198/0 PASS。

---

## なぜ 「反証機械」 か

生成は 飽和する。 refutation は 飽和しない。

現行 LLM は 流暢だ。 もっともらしい 証明の 筋道、 もっともらしい code、 もっともらしい 定理の 名前を、 事実かどうかと 独立に 出力できる。 benchmark が 96% まで 飽和しても、 この構造は 変わらない。 世界に 足りないのは 「もっともらしいものを 作る 機械」 ではなく、 **「もっともらしいものを 確実に殺す 機械」** の 方だ。

反証機械の core promise:

- 主張を受け取ったら、 **反例探索に 計算資源を 割く**。 証明の試みは 後回し。
- 反例が 見つからなかった場合、 **「見つからなかった 探索空間の 形」** を 明示的に return (沈黙を 成功と 偽装しない)。
- 出力に 必ず 「その主張が 偽なら 壊れる 場所」 が 添付される。 Lean 4 の `sorry` ゼロは これの 最も厳格な 特殊ケース。
- **「反証できなかった」** と **「正しい」** を、 型 level で 別物 として 扱う。

---

## 4-value verdict (「絶対に嘘をつかない」 core discipline)

```python
class Verdict(str, Enum):
    CONFIRMED = "confirmed"           # post-condition PASS + marker 空
    REFUTED = "refuted"               # 具体的な counter-witness が 得られた
    HOLDING = "holding"               # counter-witness 未発見 かつ marker 非空
    INCOMPLETE_FRAME = "incomplete_frame"  # 主張自体が well-formed でない
```

Binary TRUE/FALSE に しない = **反証されなかった ≠ 正しい**。 IUT 12 年 holding discipline の 型化。

**「沈黙を 成功と 偽装しない」 型的保証** (正確な範囲):
- `REFUTED` / `HOLDING` / `INCOMPLETE_FRAME` の 3 verdict は **`IncompleteMarker` 1 個以上 必須** = `VerdictWithMarkers` dataclass invariant で ValueError raise (0.1.0a1 実測で 3 verdict 全 型拒否 verify 済)
- `CONFIRMED` は marker 不要 = 型 level では marker 空 CONFIRMED 構築を 拒否しない
- **CONFIRMED の 信頼性は ツール層の 規律で 保証**: `search_counterexample` / `assert_breakpoints` / `hold_verdict` は 構造上 常に marker を 出すため CONFIRMED に 到達不能、 `refute_lean_source` のみが Lean 4 kernel の sorry-free 認定を 経由して CONFIRMED を 返す設計
- caller が `VerdictWithMarkers(verdict=CONFIRMED, markers=[], ...)` を 直接構築することは 型的には 可能 (0.1.0a2 findings ① 明示訂正、 従前 「型 level で 保証」 は 過大主張)

---

## 4 primitives (`rei_verify`)

| primitive | 役割 |
|---|---|
| `Verdict` | 4 値 enum |
| `IncompleteMarker` | dimension 4 種語彙 (`search_space` / `witness_type` / `compute_budget` / `frame`) + 全 field 非空 required |
| `AuditChain` | sha256 hash-chained append-only JSONL + tamper detection (`verify()` で `broken_at` index) |
| `VerifiedExecution` | pre-check + action + post-check + audit を **atomic に 束ねる** context |

## 4 refutation tools (`rei_verify.*`)

反証機械の 心臓部。 全 tool が `VerdictWithMarkers` (4 値 verdict + markers + audit_hashes) を return する 一貫 shape。

| tool | module | 意味 | verdict pattern |
|---|---|---|---|
| **`refute_lean_source`** | `.refute` | Lean 4 source を 実行、 sorry / native_decide / disallowed axiom を verify | CONFIRMED / REFUTED / HOLDING / INCOMPLETE_FRAME |
| **`search_counterexample`** | `.search` | iterable space + callable predicate で 反例探索 | REFUTED / HOLDING / INCOMPLETE_FRAME (never CONFIRMED) |
| **`assert_breakpoints`** | `.breakpoint` | N labeled cases × 個別 logic の 網羅検査 | REFUTED / HOLDING / INCOMPLETE_FRAME (never CONFIRMED) |
| **`hold_verdict`** | `.hold` | 宣言的 HOLDING 生成 (「保留の 型化」) | HOLDING / INCOMPLETE_FRAME (only) |

**★ CONFIRMED を tool が 出すのは `refute_lean_source` のみ** (Lean 4 kernel が sorry-free 認定した case のみ)。 他 3 tool は 常に REFUTED か HOLDING = **「absence of counter-example is not proof」** discipline の 型 level 保証。

## 8 MCP tools

Claude Desktop / Cursor / Cline 等の LLM client から 直接呼べる:

| tool | 用途 |
|---|---|
| `create_audit_chain` | named audit chain 作成 |
| `append_audit_entry` | raw entry 追記 |
| `verify_audit_chain` | integrity walk + tamper 検出 |
| `record_verdict` | 4 値 verdict + markers を 単純追記 (invariant enforced) |
| `refute_lean` | Lean 4 source 検証 |
| `search_counterexample_explicit` | 反例探索 (`x` bind expression + samples list) |
| `assert_breakpoints_explicit` | 網羅検査 (`ctx` bind expression + labeled dicts) |
| `hold_verdict_tool` | 宣言的 HOLDING |

MCP-safe expression は **restricted eval** = `__import__` / `exec` / `eval` / `open` / `__` prefix 事前 reject、 `_SAFE_BUILTINS` whitelist (`abs`/`min`/`max`/`sum`/`len`/`int`/`float`/`str`/`bool`/`round`/`any`/`all`/`range`) のみ 許可。

---

## Installation

```bash
pip install rei-verify           # core primitives (no external deps)
pip install rei-verify[mcp]      # + MCP server
```

or from source:

```bash
git clone https://github.com/fc0web/rei-verify.git
cd rei-verify
pip install -e .[mcp]
```

Requires Python 3.10+ (dataclass + Enum + typing 新機能)。 core primitives は 標準 library のみ で 動作 (mcp package 不在 でも import OK)。

---

## Usage — library

### VerifiedExecution (custom)

```python
from pathlib import Path
from rei_verify import (
    Verdict, IncompleteMarker, PostCheckResult,
    VerifiedExecution, AuditChain,
)

audit = AuditChain(Path("./reasoning.jsonl"))
ve = VerifiedExecution(
    claim="1 + 1 == 2",
    pre_check=lambda: True,
    post_check=lambda r: PostCheckResult(refuted=(r != 2), markers=[]),
    audit=audit,
)
result = ve.run(lambda: 1 + 1)
# result.verdict == Verdict.CONFIRMED
# result.audit_hashes == [h1, h2, h3, h4, h5]  # 5 phase entry
```

### refute_lean_source

```python
from rei_verify.refute import refute_lean_source

result = refute_lean_source(
    claim="trivial True holds",
    lean_source="theorem trivial_true : True := trivial\n",
    audit=audit,
    theorem_name="trivial_true",
    timeout_sec=60,
)
# result.verdict == Verdict.CONFIRMED  (axiom-free, ~1200 ms)
```

Default allow_axioms = Mathlib base `[propext, Classical.choice, Quot.sound]`。 sorry / native_decide / disallowed axiom は HOLDING に routing。

### search_counterexample

```python
from rei_verify.search import search_counterexample

result = search_counterexample(
    claim="no n in [1,100] equals 42",
    predicate=lambda x: x == 42,
    space=range(1, 101),
    audit=audit,
    space_description="range(1, 101)",
)
# result.verdict == Verdict.REFUTED  (witness marker: n=42)
```

- exhaustion → HOLDING (search_space marker、 「absence ≠ proof」)
- time/sample budget → HOLDING (compute_budget marker)

### assert_breakpoints

```python
from rei_verify.breakpoint import Breakpoint, assert_breakpoints

result = assert_breakpoints(
    claim="Collatz t1=1 orbits descend",
    breakpoints=[
        Breakpoint("n=27", assertion=lambda: descent(27), context={"n": 27}),
        Breakpoint("n=703", assertion=lambda: descent(703), context={"n": 703}),
        Breakpoint("n=6171", assertion=lambda: descent(6171), context={"n": 6171}),
    ],
    audit=audit,
    stop_on_first_failure=True,  # False で 全 breakpoint 実行 (集計目的)
)
```

- 任意 breakpoint False → REFUTED (label + context を witness)
- 全 pass → HOLDING (「listed checkpoints exhausted ≠ 全 case cover」)

### hold_verdict

```python
from rei_verify.hold import hold_verdict

result = hold_verdict(
    claim="my analytical claim under investigation",
    markers=[
        IncompleteMarker(
            dimension="search_space",
            what_was_tried="5 counterexample approaches",
            what_was_not_tried="structural refutation via categorical semantics",
            reason="categorical angle deferred to next session",
        ),
    ],
    audit=audit,
    notes="manual reasoning pause",
    require_multi_dimension=True,  # 単一 dim なら augmentation marker 追加
)
# result.verdict == Verdict.HOLDING  (audit chain 4 phase entries + caller markers)
```

---

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

or (installed script):

```json
{
  "mcpServers": {
    "rei-verify": {
      "command": "rei-verify"
    }
  }
}
```

**MCP expression 例** (`x` bind for search, `ctx` bind for breakpoints):

```json
{
  "tool": "search_counterexample_explicit",
  "arguments": {
    "chain_id": "chain-abc123",
    "claim": "no perfect square in [1,100] equals 42",
    "samples": [1, 4, 9, 16, 25, 36, 49, 64, 81, 100],
    "predicate_expr": "x == 42",
    "space_description": "perfect squares up to 100"
  }
}
```

```json
{
  "tool": "assert_breakpoints_explicit",
  "arguments": {
    "chain_id": "chain-abc123",
    "claim": "Collatz t1=1 descent",
    "breakpoints": [
      {"label": "n=27 case", "assertion_expr": "ctx['descent'] < 0",
       "context": {"n": 27, "descent": -0.5}},
      {"label": "n=703 case", "assertion_expr": "ctx['descent'] < 0",
       "context": {"n": 703, "descent": 0.2}}
    ]
  }
}
```

---

## Integration demo

`examples/collatz_t1_ones_lyapunov_demo.py` — Collatz 奇数 n with trailing_ones(n)=1 の Lyapunov α-descent scan を assert_breakpoints で 実行。

```bash
python examples/collatz_t1_ones_lyapunov_demo.py
```

Sample output (1,048,575 samples / 76.7 ms):

```
α=0.5〜0.85: WITNESS  n=9         r(n)=0.885622  → α refuted ✓
α=0.9:       WITNESS  n=17        r(n)=0.905315  → α refuted ✓
α=0.93:      WITNESS  n=57        r(n)=0.930288  → α refuted ✓
α=0.95:      WITNESS  n=313       r(n)=0.950121  → α refuted ✓
α=0.97:      WITNESS  n=14,601    r(n)=0.970001  → α refuted ✓
α=0.99:      NO WITNESS in range  max r=0.981135 < 0.99  → α NOT refuted in sample

VERDICT: REFUTED  (α=0.99 が サンプル範囲 で 未 refute = finite absence report)
audit chain: 6 entries、 sha256 hash chain intact
```

Witness n が α tight 化と共に 増大 (n=9 → n=14,601) = **r(n) → 1 as n → ∞** の 有限反映を 直接観測、 tool が **「finite absence を CONFIRMED に 自動昇格しない」** discipline を 遵守した実例。 詳細な honest scope は demo 内 コメント参照。

---

## Test coverage

**累計 198/0 PASS** (6 test files):

| file | assert | 内容 |
|---|---:|---|
| `test_skeleton.py` | 37 | Verdict + IncompleteMarker + PostCheckResult + VerdictWithMarkers + AuditChain + VerifiedExecution invariants + 4-verdict paths |
| `test_mcp_layer.py` | 30 | tool 直接 invoke + validation + tamper detection + smoke registration |
| `test_refute.py` | 22 | parse_lean_axioms + classify_axioms + pre-check + **live smoke (Lean 4.33)** |
| `test_search.py` | 37 | 4 exit path + per-sample error + restricted eval safety (8 hostile expr reject) + MCP tool |
| `test_breakpoint.py` | 33 | pre-check + verdict paths + stop_on_first_failure + time budget + var_name extension |
| `test_hold.py` | 39 | pre-check + valid HOLDING + require_multi_dimension + invariant + MCP + 4-tool shape consistency |

```bash
# individual
python test/test_skeleton.py
python test/test_refute.py       # requires 'lean' on PATH for live smoke

# all
for f in test/test_*.py; do PYTHONIOENCODING=utf-8 python -u "$f" | tail -3; done
```

---

## For maintainers: PyPI Trusted Publisher setup

本 repo の `.github/workflows/publish.yml` は **v* tag push で PyPI 本番 publish** + **workflow_dispatch で TestPyPI dry-run**。 使用前に PyPI / TestPyPI 両側の Trusted Publisher 登録 + GitHub Environment 作成が 必要。

詳細手順は [`TRUSTED_PUBLISHER_SETUP.md`](TRUSTED_PUBLISHER_SETUP.md) 参照。

**⚠️ tag push 前 に workflow.yml + Trusted Publisher 登録 の double-check を** (「tag = release trigger」 事故防止)。

---

## Design

See [`DESIGN.md`](DESIGN.md) for full rationale (8 節):
- 設計の 起点 (Rei stack 4 原則)
- 4 primitives 詳細
- Verdict rule table (単一 source of truth)
- 反証機械 3+1 tool mapping
- framing-drift-detector との 関係
- 非目標 (out of scope for skeleton)
- 依存 (external dep ゼロ の 意図)
- honest scope

---

## Related

- [fc0web/rei-automator-mcp](https://github.com/fc0web/rei-automator-mcp) — Windows PC 自動化 MCP、 AuditChain の 由来 (STEP 1340 AuditLogWriter の 汎用抽出)
- [fc0web/grounded](https://github.com/fc0web/grounded) — 散文 grounding checker (2-tier verification)
- [fc0web/grounding-check](https://github.com/fc0web/grounding-check) — SCPI hardware grounding checker

---

## Author

藤本 伸樹 (Nobuki Fujimoto)

## License

MIT (v0.x irrevocable)。 v1.0+ で AGPL-3.0 + commercial dual 可能性。 [`LICENSE`](LICENSE) 参照。

---

## Honest scope (譲れない線)

- (i) skeleton の refutation tools は Lean 4 との 直接連携 (single-file `lean` 実行) のみ、 Mathlib 依存 proof は 別 iter (lake project 経由)
- (ii) `IncompleteMarker.dimension` 語彙は 初期 4 種のみ、 拡張は operational 経験から
- (iii) hash chain は tamper detection 用、 cryptographic signing (Sigstore 等) は 別 concern
- (iv) restricted eval は AST-level analysis (asteval 等) より 弱い、 高信頼要件 は 別 iter で 依存追加
- (v) 「反証機械」 の 新規性主張 ゼロ (`[[feedback-world-uniqueness-claim-controllable]]`) = property-based testing (Hypothesis) + Lean 4 sorry-check + Coq / Isabelle 系 industry 標準 の 統合 discipline layer のみ、 novelty は 「4 値 verdict + marker invariant + hash chain の 型的統合 + MCP wrapper」 の 組合せ discipline のみ
- (vi) integration demo (Collatz t1=1) は 藤本さん 実 リヤプノフ解析 の **再現 では ない** — 簡略化 V = log2(n) と 有限 sample での **TOOL 動作 の 実証** のみ、 真 reproduction は 藤本さん 実 V + 条件 + Lean 4 formalization 経由 で 別 iter
- (vii) `refute_lean` の "sorry-free" 判定は `#print axioms` 依存 = Lean 自体の kernel bug が あれば verify されず (kernel bug は Rei scope 外)
