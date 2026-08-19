# rei-verify — 共通 infrastructure design (skeleton)

> **Target**: 反証機械 (refutation machine) 完成の 心臓部を 支える primitives。
> **Author**: 藤本 伸樹 (Nobuki Fujimoto) + Claude Code
> **Started**: 2026-08-19

## 設計の 起点

Rei stack が 育ててきた 4 原則を 「型」 として 固定する:

1. **[[feedback-zero-sorry-floor-not-ceiling]]** — sorry ゼロ は 「反証されなかった」 の 最も厳格な特殊ケース
2. **「沈黙を 成功と 偽装しない」** ([[feedback-one-reproduction-over-ten-unverified]]) — 探索が 尽きたら 尽きたと 明示する
3. **STEP 1276 `judgmentStatus` 4 値** = `confirmed / pending / refuted / holding` の 型化
4. **STEP 1340 AuditLogWriter** = hash chain append-only JSONL による 事実の 改竄不能性

これら 4 原則を、 refutation-machine と framing-drift-detector の 両方が 共有する **4 primitives** に 蒸留する。

## 4 primitives

### 1. `Verdict` (4 値 enum)

```python
class Verdict(str, Enum):
    CONFIRMED = "confirmed"          # post-condition PASS + incomplete_markers 空
    REFUTED = "refuted"              # 具体的な counter-witness が 得られた
    HOLDING = "holding"              # counter-witness 未発見 かつ incomplete_markers 非空
    INCOMPLETE_FRAME = "incomplete_frame"  # 主張自体が well-formed でない (pre-check fail)
```

- **binary (TRUE/FALSE) にしない 理由**: 反証されなかった = 正しい ではない ([[feedback-collatz-not-shannon-kolmogorov-analog-2026-08-11]] の 精神)
- **`HOLDING` を CONFIRMED に 自動昇格しない**: IUT 12 年 holding の operational 型化 (chat-Claude 2026-08-06 turn 教訓)
- **`INCOMPLETE_FRAME` を REFUTED と区別**: 「反証したくても そもそも 反証対象になっていない」 の 明示 (Rei-Solver 経路 A/B/C の 精神)
- D-FUMT₈ mapping: CONFIRMED=TRUE / REFUTED=FALSE / HOLDING=NEITHER / INCOMPLETE_FRAME=ZERO (vacuous)

### 2. `IncompleteMarker`

```python
@dataclass(frozen=True)
class IncompleteMarker:
    dimension: str          # "search_space" | "witness_type" | "compute_budget" | "frame"
    what_was_tried: str     # 何を試したか
    what_was_not_tried: str # 何が 試されずに 残ったか (「見つからなかった探索空間の形」)
    reason: str             # なぜ 試さなかったか
```

**Invariant** (`VerdictWithMarkers` dataclass __post_init__ で 強制):
- `Verdict != CONFIRMED` の 全ての verdict に **1 個以上** の marker が 必須 → 違反で `ValueError`
- CONFIRMED は marker 不要 = 型 level では marker 空 CONFIRMED 構築を 拒否しない
- 「incomplete と言いつつ CONFIRMED」 は type error にならない (0.1.0a2 findings ① 明示訂正)

**「沈黙を 成功と偽装しない」 discipline の 正確な範囲**:
- **型 level 強制** = REFUTED / HOLDING / INCOMPLETE_FRAME の 3 verdict (marker 必須)
- **ツール層 規律で 保証** = CONFIRMED (search / breakpoint / hold は 構造上 marker を 出すため CONFIRMED に 到達不能、 refute_lean のみ Lean 4 kernel sorry-free 認定を 経由)
- caller が `VerdictWithMarkers(verdict=CONFIRMED, markers=[], ...)` を 直接構築することは 型的には 可能 = 「型 level で 保証」 は 3 verdict 側のみ。 0.1.0a1 findings ① で 明示訂正。

### 3. `AuditChain`

STEP 1340 rei-automator-mcp AuditLogWriter の 汎用抽出:

```python
class AuditChain:
    def __init__(self, path: Path): ...
    def append(self, entry: dict) -> str:      # return hash of new entry
    def verify(self) -> ChainVerification:      # walk, detect tamper
    def get_entries(self) -> list[dict]: ...
```

- **格納**: `data/rei-verify-audit/{run_id}.jsonl` (JSONL append-only)
- **hash algorithm**: sha256, prev_hash + json.dumps(entry, sort_keys=True) → new_hash
- **tamper detection**: `verify()` は 破綻点 index (brokenAt) を 返す

### 4. `VerifiedExecution`

pre-check + action + post-check を **atomically audit** で 束ねる context manager。

```python
class VerifiedExecution:
    def __init__(
        self,
        claim: str,                          # 検証対象の 主張
        pre_check: Callable[[], bool],       # 主張の setup 前提が 満たされているか
        post_check: Callable[[Any], PostCheckResult],  # action 実行後、反証されたか
        audit: AuditChain,
        markers_from_incomplete: Callable[[], list[IncompleteMarker]] = lambda: [],
    ): ...

    def run(self, action: Callable[[], Any]) -> VerdictWithMarkers: ...
```

**Verdict 判定 rule** (単一 source of truth):

| pre_check | action | post_check | Verdict |
|---|---|---|---|
| False | (not run) | (not run) | **INCOMPLETE_FRAME** + marker |
| True | raises | classify_exception | REFUTED (if exception is counter-witness) / HOLDING (if timeout/budget) |
| True | success | returns `(True, [])` | **CONFIRMED** |
| True | success | returns `(False, [counter_witness])` | **REFUTED** + witness marker |
| True | success | returns `(False, incomplete_markers)` | **HOLDING** + markers |

全 step は audit chain に append される (`pre_check_result` / `action_started` / `action_ended` / `verdict`)。

## 反証機械 3+1 tool との mapping

chat-Claude 提案 + 私の 4 番目:

| tool | uses |
|---|---|
| `refute_lean` | `VerifiedExecution` (pre=source_clean, post=`sorry_count == 0`) → CONFIRMED or REFUTED |
| `search_counterexample` | `VerifiedExecution` (post=exhaust_check with incomplete_markers) → REFUTED (witness) or HOLDING (exhausted budget) |
| `assert_breakpoint` | `VerifiedExecution` (post=`identifies_breakpoint`) → structured breakpoint or HOLDING |
| **`hold_verdict`** (私追加) | 直接 `Verdict.HOLDING` を 生成 + 明示的 marker list、 「N 候補試行、いずれも 一般命題を殺さず、私の 反証試行が incomplete である 理由は これこれ」 |

## framing-drift-detector tool との mapping (bonus consumer)

| tool | uses |
|---|---|
| `check_scope_drift` | `VerifiedExecution` (pre=previous_scope_matches_current, post=action_within_scope) |
| `verify_side_effects` | `VerifiedExecution` (pre=action_plan_declared, post=actual_side_effects ⊆ declared) |

## 非目標 (out of scope for skeleton)

- LLM integration (chat-Claude / GPT / Gemini 系) — 反証は 決定論的 side effect のみ
- MCP protocol wrapper — 次 iteration
- Persistence layer (SQLite / graph DB) — JSONL で 十分、graph は 別 concern
- Distributed execution — single-machine で 十分

## 依存

- Python 3.10+ (dataclass + Enum + type hint)
- 標準 library のみ (`json`, `hashlib`, `pathlib`, `enum`, `dataclasses`, `datetime`)
- 外部依存 ゼロ (skeleton 段階)

## 検証

- `test/test_core.py` = Verdict / IncompleteMarker / VerifiedExecution 単体
- `test/test_audit.py` = AuditChain hash chain integrity + tamper detection
- 最低 25 assertion、 全 4 Verdict + tamper detection + marker invariant を 網羅

## honest scope

- (i) skeleton は Lean 4 と 直接連携 しない — `refute_lean` の 中身は 別 iteration
- (ii) `IncompleteMarker` の dimension 語彙は 初期 4 種のみ、 拡張は operational 経験から
- (iii) hash chain は tamper detection 用、 cryptographic signing (Sigstore 等) は 別 concern
- (iv) 「反証機械」 の 新規性 主張なし ([[feedback-world-uniqueness-claim-controllable]]) — property-based testing (Hypothesis) + Lean 4 sorry-check + Coq / Isabelle 系 industry 標準を 統合する discipline layer のみ

## 関連

- [[project-rei-automator-mcp-v020a2-a3-arc-2026-08-18]] (AuditChain 由来 STEP 1340)
- [[feedback-zero-sorry-floor-not-ceiling]] (CONFIRMED verdict の 意味)
- [[feedback-one-reproduction-over-ten-unverified]] (marker invariant の 由来)
- STEP 1276 (judgmentStatus 4 値 = Verdict 4 値の 直接対応)
- IUT arc 2026-08-06 (HOLDING の 12 年 discipline)
