# -*- coding: utf-8 -*-
"""EvidenceRepository (Task 42)。

本仓储是"证据是内容寻址的"这条不变量在**存储层**的落点:

- ``canonical_key`` 列上有 ``UNIQUE`` 约束。``compute_canonical_key()``
  (Task 23) 是去重身份, 因此"同一来源 + 同一位置 + 同一内容"在数据库里
  **不可能**出现两行 —— 即使有人绕过仓储直接写 SQL。
- 主键仍是 ``evidence_id`` (也由内容派生)。同一 canonical key 配不同
  ``evidence_id`` 会被 UNIQUE 拦下, 并翻译成 ``DUPLICATE_RECORD``
  (-> ``CONFLICT``), 与领域层的 ``AddStatus.DUPLICATE`` 语义一致。
- ``insertion_seq`` 保存插入顺序。Task 23 的查询是"确定性 (插入顺序)",
  而 SQLite 不保证无 ``ORDER BY`` 的行序, 所以顺序必须**显式存列**,
  否则重启后查询顺序会变。

本模块从 ``src.evidence_store`` **只导入纯函数与枚举** (不导入 sqlite3
到那个模块), 因此 ``test_evidence_store.py::TestScopeAudit`` 仍然全绿。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from src.evidence_store import EvidenceState, compute_canonical_key
from src.models import Evidence
from src.persistence.repositories.base import DocumentRepository

__all__ = ["EvidenceRepository"]


def _evidence_columns(
    evidence: Evidence,
    *,
    state: str,
    insertion_seq: int,
) -> dict[str, Any]:
    reference = evidence.source_reference
    return {
        "evidence_id": evidence.evidence_id,
        "canonical_key": compute_canonical_key(evidence),
        "material_id": (reference.material_id if reference is not None else None),
        "language": evidence.language.value,
        "confidence": evidence.confidence.value,
        "evidence_type": evidence.evidence_type.value,
        "state": state,
        "content": evidence.content,
        "insertion_seq": int(insertion_seq),
    }


class EvidenceRepository(DocumentRepository):
    """证据仓储 (内容寻址, 追加式)。"""

    table = "evidence"

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def save(
        self,
        evidence: Evidence,
        *,
        state: EvidenceState | str = EvidenceState.ACTIVE,
        insertion_seq: Optional[int] = None,
    ) -> None:
        """保存一条证据 (幂等: 同 canonical key 只更新同一行)。"""
        if not isinstance(evidence, Evidence):
            raise TypeError(f"expected Evidence, got {type(evidence).__name__}")
        if insertion_seq is None:
            insertion_seq = self.next_insertion_seq()
        state_value = state.value if isinstance(state, EvidenceState) else str(state)
        self.put(
            _evidence_columns(
                evidence, state=state_value, insertion_seq=int(insertion_seq)
            ),
            evidence,
        )

    def save_many(
        self,
        evidences: Iterable[Evidence],
        *,
        state: EvidenceState | str = EvidenceState.ACTIVE,
    ) -> int:
        """按给定顺序批量保存, 顺序即 ``insertion_seq``。"""
        state_value = state.value if isinstance(state, EvidenceState) else str(state)
        seq = self.next_insertion_seq()
        count = 0
        for evidence in evidences:
            self.save(evidence, state=state_value, insertion_seq=seq)
            seq += 1
            count += 1
        return count

    def next_insertion_seq(self) -> int:
        value = self.database.scalar(
            f"SELECT COALESCE(MAX(insertion_seq), -1) + 1 FROM {self.table}",
            (),
            default=0,
        )
        return int(value or 0)

    def set_state(self, evidence_id: str, state: EvidenceState | str) -> bool:
        """切换生命周期状态 (ACTIVE / RETIRED); 不删除任何内容。"""
        state_value = state.value if isinstance(state, EvidenceState) else str(state)
        cursor = self.database.execute(
            f"UPDATE {self.table} SET state = ? WHERE evidence_id = ?",
            (state_value, evidence_id),
        )
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def load(self, evidence_id: str) -> Optional[Evidence]:
        payload = self.get(evidence_id)
        if payload is None:
            return None
        return Evidence.from_dict(payload)

    def load_all(self, *, include_retired: bool = True) -> list[Evidence]:
        """全部证据, 按 ``insertion_seq`` (即插入顺序) 确定性排序。"""
        where = None if include_retired else "state = 'ACTIVE'"
        return [Evidence.from_dict(p) for p in self.all(where=where)]

    def load_active(self) -> list[Evidence]:
        return self.load_all(include_retired=False)

    def state_of(self, evidence_id: str) -> Optional[str]:
        row = self.get_row(evidence_id)
        if row is None:
            return None
        return str(row["state"])

    def canonical_key_of(self, evidence_id: str) -> Optional[str]:
        row = self.get_row(evidence_id)
        if row is None:
            return None
        return str(row["canonical_key"])

    def find_by_canonical_key(self, canonical_key: str) -> Optional[str]:
        """按去重身份找已存在的 ``evidence_id`` (幂等判定用)。"""
        row = self.database.query_one(
            f"SELECT evidence_id AS e FROM {self.table} WHERE canonical_key = ?",
            (str(canonical_key),),
        )
        return None if row is None else str(row["e"])

    def insertion_order(self) -> list[str]:
        """``evidence_id`` 列表, 按插入顺序 (与 ``EvidenceStore`` 一致)。"""
        return list(self.keys())

    def material_id_of(self, evidence_id: str) -> Optional[str]:
        row = self.get_row(evidence_id)
        if row is None:
            return None
        value = row["material_id"]
        return None if value is None else str(value)

    def load_for_material(self, material_id: str) -> list[Evidence]:
        """某材料产生的证据, 按插入顺序 (溯源查询)。"""
        return [
            Evidence.from_dict(p)
            for p in self.all(
                where="material_id = ?", params=(material_id,)
            )
        ]

    def content_of(self, evidence_id: str) -> Optional[str]:
        """原始内容 (逐字, 用于证明存储层不改写原文)。"""
        row = self.get_row(evidence_id)
        if row is None:
            return None
        return row["content"]

    def snapshot(self) -> dict[str, Any]:
        """``EvidenceStore`` 兼容的快照 (用于重建内存仓储)。"""
        return {
            "evidence": [e.to_dict() for e in self.load_all()],
            "states": {
                str(r["evidence_id"]): str(r["state"]) for r in self.rows()
            },
        }

    def state_map(self) -> Mapping[str, str]:
        return {str(r["evidence_id"]): str(r["state"]) for r in self.rows()}
