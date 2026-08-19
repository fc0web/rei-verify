"""
rei-verify audit.

Hash-chained append-only JSONL log.
Pattern: STEP 1340 rei-automator-mcp AuditLogWriter の 汎用抽出。

Invariant:
  - append() は 常に 新 entry hash を 返す
  - verify() は tamper detection (broken_at index or None)
  - hash algorithm v2 (0.1.0a2+):
      sha256(hash_version || \\n || seq || \\n || prev_hash || \\n ||
             json.dumps(entry, sort_keys=True))
    hash 入力に seq + prev_hash + hash_version が含まれるため、 行オブジェクト
    最上位への キー注入 が verify() で 検出される (0.1.0a1 の findings ② 修正)。
  - verify() は 行オブジェクトの キー集合を {seq, prev_hash, entry, hash, hash_version}
    に限定 = 予期しない 追加キー を broken_at で 検出。

BREAKING CHANGE (0.1.0a1 → 0.1.0a2):
  hash algorithm v1 (0.1.0a1) は seq / prev_hash を hash 入力に含まなかった。
  v1 で書かれた chain file は 0.1.0a2 の verify() を pass しない (hash_version 欠落 で
  early error return)。 alpha 版として 破壊的変更を 明示、 既存 chain の 移行 policy は
  「新規 chain として 再作成」 (audit trail は 通常 short-lived、 permanent archive でない)。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path


GENESIS_HASH = "0" * 64  # SHA-256 の 空 chain 前 hash
HASH_VERSION = 2         # 0.1.0a2+ hash algorithm version
_ALLOWED_LINE_KEYS = frozenset({"seq", "prev_hash", "entry", "hash", "hash_version"})


@dataclass(frozen=True)
class ChainVerification:
    """AuditChain.verify() の 返り値。

    ok=True かつ entry_count > 0 なら 全 chain 健全。
    broken_at != None の場合、 その index の entry から chain が破綻している。
    """

    ok: bool
    entry_count: int
    broken_at: int | None
    last_hash: str
    reason: str


class AuditChain:
    """Hash-chained append-only JSONL log。

    File format (1 line per entry、 0.1.0a2 hash v2):
      {"hash_version": 2, "seq": 0, "prev_hash": "0...0", "entry": {...}, "hash": "abcd..."}
      {"hash_version": 2, "seq": 1, "prev_hash": "abcd...", "entry": {...}, "hash": "efgh..."}
      ...

    File is created on first append if parent dir exists.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._last_hash: str = GENESIS_HASH
        self._entry_count: int = 0
        self._load_state()

    def _load_state(self) -> None:
        """既存 file から last_hash と entry_count を restore (再起動対応)。"""
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    obj = json.loads(line)
                    self._last_hash = obj["hash"]
                    self._entry_count = obj["seq"] + 1
        except (json.JSONDecodeError, KeyError):
            # 破損 file は 触らず (verify() で broken_at を 検出させる)
            pass

    @staticmethod
    def _hash_step(hash_version: int, seq: int, prev_hash: str, entry_json: str) -> str:
        """hash algorithm v2 (0.1.0a2+): hash_version + seq + prev_hash + entry_json。

        seq と prev_hash を hash 入力に含めることで、 行オブジェクト最上位への
        キー注入 (findings ②) が hash mismatch として 検出可能になる。
        """
        h = hashlib.sha256()
        h.update(str(hash_version).encode("utf-8"))
        h.update(b"\n")
        h.update(str(seq).encode("utf-8"))
        h.update(b"\n")
        h.update(prev_hash.encode("utf-8"))
        h.update(b"\n")
        h.update(entry_json.encode("utf-8"))
        return h.hexdigest()

    def append(self, entry: dict) -> str:
        """entry を 追記、 新 hash を 返す。 file / parent dir が 無ければ 作成。"""
        # parent dir を 事前作成 (「file が 無ければ 作る」 を 全 caller で 統一)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        entry_json = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        new_hash = self._hash_step(
            HASH_VERSION, self._entry_count, self._last_hash, entry_json,
        )

        line_obj = {
            "hash_version": HASH_VERSION,
            "seq": self._entry_count,
            "prev_hash": self._last_hash,
            "entry": entry,
            "hash": new_hash,
        }

        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line_obj, ensure_ascii=False, sort_keys=True))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())

        self._last_hash = new_hash
        self._entry_count += 1
        return new_hash

    def get_entries(self) -> list[dict]:
        """全 entry (raw) を list で 返す。 破損 line は skip。"""
        out: list[dict] = []
        if not self.path.exists():
            return out
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    def verify(self, expect_at_least: int | None = None) -> ChainVerification:
        """chain 全体を walk して tamper detection。

        broken_at = None : 全 chain 健全 (or 末尾切り詰め検出時 = None、 tail truncation
                           は 「line が 破損」 でなく 「entry が 消失」 なので broken_at
                           には 該当 index がない、 reason text で 区別可能)
        broken_at = int  : その index の entry から先が 改竄されている疑い

        Args:
          expect_at_least: chain entry count の 期待 下限。 None (default) = 長さ check なし
                           (0.1.0a2 従来動作)。 int 指定時 は chain 内部整合 + entry_count
                           >= expect_at_least の 両方 verify。 不足時は ok=False + reason
                           に 「tail truncation detected」 明示 return。

        検出項目:
          - JSON parse fail
          - 行オブジェクト キー集合 が {seq, prev_hash, entry, hash, hash_version}
            以外 = 予期しない 追加キー 注入 (0.1.0a2 findings ② 修正)
          - hash_version 欠落 = pre-0.1.0a2 format (破壊的変更、 chain 再作成が必要)
          - seq mismatch (順序 or 削除)
          - prev_hash mismatch (再 hash 辻褄合わせ を 次段で 検出)
          - hash mismatch (hash_version + seq + prev_hash + entry の いずれかが 改竄で 検出)
          - tail truncation (0.1.0a3、 expect_at_least 指定時のみ = external anchor 経由)

        ★ 構造的限界 (0.1.0a3 findings ④ 明示):
          hash chain は 「過去の改変」 を 検出できるが 「未来の不在」 を 検出できない。
          末尾 entry を 削除しても 残り部分は 内部整合が とれた状態 = expect_at_least なし
          の verify() は ok=True を 返す (limitation)。 完全性 (completeness) 要件 は:
            (a) expect_at_least=N opt-in (本実装、 最軽量 external anchor)
            (b) 最終 hash を 別媒体 に 記録 + 突合 (application layer で 実装、 未提供)
            (c) 末尾 seal entry pattern (application layer で 実装、 未提供)
          詳細は DESIGN.md § Known limitations 参照。
        """
        if not self.path.exists():
            # empty chain: honor expect_at_least even in this early-return branch
            if expect_at_least is not None and expect_at_least > 0:
                return ChainVerification(
                    ok=False,
                    entry_count=0,
                    broken_at=None,
                    last_hash=GENESIS_HASH,
                    reason=(
                        f"tail truncation detected: expected >= {expect_at_least} entries, "
                        "got 0 (file does not exist). Hash chain cannot detect 'future absence' "
                        "by its own structure; external anchor (expect_at_least param, external "
                        "hash ledger, or seal entry pattern) is required for completeness "
                        "verification (see DESIGN.md § Known limitations)."
                    ),
                )
            return ChainVerification(
                ok=True,
                entry_count=0,
                broken_at=None,
                last_hash=GENESIS_HASH,
                reason="empty (file does not exist)",
            )

        prev_hash = GENESIS_HASH
        seq_expected = 0

        with self.path.open("r", encoding="utf-8") as f:
            for line_no, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    return ChainVerification(
                        ok=False,
                        entry_count=seq_expected,
                        broken_at=line_no,
                        last_hash=prev_hash,
                        reason=f"line {line_no}: JSONDecodeError",
                    )

                # hash_version check (pre-0.1.0a2 format 検出)
                if "hash_version" not in obj:
                    return ChainVerification(
                        ok=False,
                        entry_count=seq_expected,
                        broken_at=line_no,
                        last_hash=prev_hash,
                        reason=(
                            f"line {line_no}: hash_version key missing "
                            "(pre-0.1.0a2 format; chain must be re-created — "
                            "breaking change in 0.1.0a2 to detect top-level key injection)"
                        ),
                    )

                # line key set strict check (findings ② 修正: 未知キー注入 検出)
                actual_keys = set(obj.keys())
                if actual_keys != _ALLOWED_LINE_KEYS:
                    unexpected = actual_keys - _ALLOWED_LINE_KEYS
                    missing = _ALLOWED_LINE_KEYS - actual_keys
                    reason_parts = [f"line {line_no}: line-object key set mismatch"]
                    if unexpected:
                        reason_parts.append(f"unexpected keys: {sorted(unexpected)}")
                    if missing:
                        reason_parts.append(f"missing keys: {sorted(missing)}")
                    return ChainVerification(
                        ok=False,
                        entry_count=seq_expected,
                        broken_at=line_no,
                        last_hash=prev_hash,
                        reason=" | ".join(reason_parts),
                    )

                # hash_version value check
                if obj["hash_version"] != HASH_VERSION:
                    return ChainVerification(
                        ok=False,
                        entry_count=seq_expected,
                        broken_at=line_no,
                        last_hash=prev_hash,
                        reason=(
                            f"line {line_no}: unsupported hash_version "
                            f"(expected {HASH_VERSION}, got {obj['hash_version']})"
                        ),
                    )

                # seq check
                if obj.get("seq") != seq_expected:
                    return ChainVerification(
                        ok=False,
                        entry_count=seq_expected,
                        broken_at=line_no,
                        last_hash=prev_hash,
                        reason=f"line {line_no}: seq mismatch (expected {seq_expected}, got {obj.get('seq')})",
                    )

                # prev_hash check
                if obj.get("prev_hash") != prev_hash:
                    return ChainVerification(
                        ok=False,
                        entry_count=seq_expected,
                        broken_at=line_no,
                        last_hash=prev_hash,
                        reason=f"line {line_no}: prev_hash mismatch",
                    )

                # hash recompute check (v2: hash_version + seq + prev_hash + entry)
                entry_json = json.dumps(obj["entry"], ensure_ascii=False, sort_keys=True)
                expected_hash = self._hash_step(
                    obj["hash_version"], obj["seq"], prev_hash, entry_json,
                )
                if obj.get("hash") != expected_hash:
                    return ChainVerification(
                        ok=False,
                        entry_count=seq_expected,
                        broken_at=line_no,
                        last_hash=prev_hash,
                        reason=f"line {line_no}: hash mismatch (tamper detected)",
                    )

                prev_hash = obj["hash"]
                seq_expected += 1

        # tail truncation check (0.1.0a3 findings ④ mitigation、 opt-in via expect_at_least)
        if expect_at_least is not None and seq_expected < expect_at_least:
            return ChainVerification(
                ok=False,
                entry_count=seq_expected,
                broken_at=None,   # 「line が 破損」 でなく 「entry が 消失」
                last_hash=prev_hash,
                reason=(
                    f"tail truncation detected: expected >= {expect_at_least} entries, "
                    f"got {seq_expected} (chain internally consistent but shorter than expected). "
                    "Hash chain cannot detect 'future absence' by its own structure; "
                    "external anchor (expect_at_least param, external hash ledger, or seal "
                    "entry pattern) is required for completeness verification "
                    "(see DESIGN.md § Known limitations)."
                ),
            )

        return ChainVerification(
            ok=True,
            entry_count=seq_expected,
            broken_at=None,
            last_hash=prev_hash,
            reason=f"ok ({seq_expected} entries, hash_version={HASH_VERSION})",
        )
