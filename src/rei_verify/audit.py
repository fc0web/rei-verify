"""
rei-verify audit.

Hash-chained append-only JSONL log.
Pattern: STEP 1340 rei-automator-mcp AuditLogWriter の 汎用抽出。

Invariant:
  - append() は 常に 新 entry hash を 返す
  - verify() は tamper detection (brokenAt index or None)
  - hash algorithm: sha256(prev_hash + json.dumps(entry, sort_keys=True))
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path


GENESIS_HASH = "0" * 64  # SHA-256 の 空 chain 前 hash


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

    File format (1 line per entry):
      {"seq": 0, "prev_hash": "0...0", "entry": {...}, "hash": "abcd..."}
      {"seq": 1, "prev_hash": "abcd...", "entry": {...}, "hash": "efgh..."}
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
    def _hash_step(prev_hash: str, entry_json: str) -> str:
        h = hashlib.sha256()
        h.update(prev_hash.encode("utf-8"))
        h.update(b"\n")
        h.update(entry_json.encode("utf-8"))
        return h.hexdigest()

    def append(self, entry: dict) -> str:
        """entry を 追記、 新 hash を 返す。 file / parent dir が 無ければ 作成。"""
        # parent dir を 事前作成 (「file が 無ければ 作る」 を 全 caller で 統一)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        entry_json = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        new_hash = self._hash_step(self._last_hash, entry_json)

        line_obj = {
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

    def verify(self) -> ChainVerification:
        """chain 全体を walk して tamper detection。

        broken_at = None : 全 chain 健全
        broken_at = int  : その index の entry から先が 改竄されている疑い
        """
        if not self.path.exists():
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

                # hash recompute check
                entry_json = json.dumps(obj["entry"], ensure_ascii=False, sort_keys=True)
                expected_hash = self._hash_step(prev_hash, entry_json)
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

        return ChainVerification(
            ok=True,
            entry_count=seq_expected,
            broken_at=None,
            last_hash=prev_hash,
            reason=f"ok ({seq_expected} entries)",
        )
