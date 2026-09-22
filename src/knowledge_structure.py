from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
from src.models import KnowledgePoint
from src.integration import ConflictRecord
class RelationType(str, Enum):
    RELATED_TO = 'related_to'
    PRE_REQUSITE_OF = 'prerequisite_of'
    TOPIC = 'topic'
    @classmethod
    def from_string(cls, value: str) -> RelationType:
        for rt in cls:
            if rt.value.lower() == value.strip().lower():
                return rt
        return cls.RELATED_TO
@dataclass
class Relationship:
    source_id: str = ''
    target_id: str = ''
    relation_type: RelationType = RelationType.RELATED_TO
    evidence_refs: list[str] = field(default_factory=list)
    relationship_id: str = ''
    def __post_init__(self) -> None:
        if not self.relationship_id:
            self.relationship_id = str(uuid.uuid4())
        if isinstance(self.relation_type, str):
            self.relation_type = RelationType.from_string(self.relation_type)
    def _canonical_key(self) -> tuple:
        if self.relation_type == RelationType.RELATED_TO:
            return ('related_to', tuple(sorted([self.source_id, self.target_id])))
        return (self.relation_type.value, self.source_id, self.target_id)
    def to_dict(self) -> dict[str, Any]:
        return {'relationship_id': self.relationship_id, 'source_id': self.source_id, 'target_id': self.target_id, 'relation_type': self.relation_type.value, 'evidence_refs': self.evidence_refs}
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Relationship:
        return cls(relationship_id=data['relationship_id'], source_id=data['source_id'], target_id=data['target_id'], relation_type=data['relation_type'], evidence_refs=data.get('evidence_refs', []))
@dataclass
class KnowledgeStructure:
    knowledge_points: dict[str, KnowledgePoint] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)
    review_records: list["ReviewRecord"] = field(default_factory=list)
    _conflict_ids: set[str] = field(default_factory=set)
    _relationship_keys: set[tuple] = field(default_factory=set)
    _review_ids: set[str] = field(default_factory=set)

    def add_conflict(self, conflict: ConflictRecord) -> bool:
        if conflict.conflict_id in self._conflict_ids:
            return False
        self.conflicts.append(conflict)
        self._conflict_ids.add(conflict.conflict_id)
        return True
    def add_knowledge_point(self, kp: KnowledgePoint) -> bool:
        if kp.knowledge_id in self.knowledge_points:
            return False
        self.knowledge_points[kp.knowledge_id] = kp
        return True

    def add_review_record(self, record) -> bool:
        """Append-only, idempotent review-record storage (Task 15).

        Deduplicates by stable review_id so resubmitting the same human
        action never creates duplicate records.
        """
        rid = getattr(record, "review_id", "")
        if not rid or rid in self._review_ids:
            return False
        self.review_records.append(record)
        self._review_ids.add(rid)
        return True

    def review_records_for_knowledge_point(self, knowledge_point_id: str) -> list:
        """Review history for one knowledge point, in stable review_id order."""
        recs = [r for r in self.review_records if r.knowledge_point_id == knowledge_point_id]
        recs.sort(key=lambda r: r.review_id)
        return recs

    def conflict_ids_for_knowledge_point(self, knowledge_point_id: str) -> list[str]:
        """Stable, de-duplicated conflict IDs touching a KnowledgePoint."""
        kp = self.knowledge_points.get(knowledge_point_id)
        if kp is None:
            return []
        refs = set(kp.evidence_refs)
        out = []
        for c in self.conflicts:
            if c.conflict_id and c.conflict_id not in out and any(ref in refs for ref in c.evidence_refs):
                out.append(c.conflict_id)
        return sorted(out)
    def add_relationship(self, rel: Relationship) -> bool:
        if rel.source_id not in self.knowledge_points:
            return False
        if rel.target_id not in self.knowledge_points:
            return False
        if rel.source_id == rel.target_id:
            return False
        if not isinstance(rel.relation_type, RelationType):
            return False
        canonical = rel._canonical_key()
        if canonical in self._relationship_keys:
            return False
        kp_source = self.knowledge_points[rel.source_id]
        kp_target = self.knowledge_points[rel.target_id]
        valid_evidence_ids = set(kp_source.evidence_refs) | set(kp_target.evidence_refs)
        for ev_ref in rel.evidence_refs:
            if not ev_ref or ev_ref not in valid_evidence_ids:
                return False
        self.relationships.append(rel)
        self._relationship_keys.add(canonical)
        return True
    def get_relationships(self, knowledge_id: Optional[str] = None) -> list[Relationship]:
        if knowledge_id is None:
            return list(self.relationships)
        return [r for r in self.relationships if r.source_id == knowledge_id or r.target_id == knowledge_id]
    def get_knowledge_point(self, knowledge_id: str) -> Optional[KnowledgePoint]:
        return self.knowledge_points.get(knowledge_id)
    def to_dict(self) -> dict[str, Any]:
        return {
            'knowledge_points': [kp.to_dict() for kp in self.knowledge_points.values()],
            'relationships': [r.to_dict() for r in self.relationships],
            'conflicts': [c.to_dict() for c in self.conflicts],
            'review_records': [rr.to_dict() for rr in self.review_records],
        }
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeStructure:
        structure = cls()
        for kp_data in data.get('knowledge_points', []):
            kp = KnowledgePoint.from_dict(kp_data)
            structure.knowledge_points[kp.knowledge_id] = kp
        for rel_data in data.get('relationships', []):
            rel = Relationship.from_dict(rel_data)
            structure.relationships.append(rel)
            structure._relationship_keys.add(rel._canonical_key())
        for c_data in data.get('conflicts', []):
            conflict = ConflictRecord(
                conflict_id=c_data.get('conflict_id', ''),
                evidence_refs=c_data.get('evidence_refs', []),
                description=c_data.get('description', ''),
                status=c_data.get('status', 'PENDING'),
            )
            structure.conflicts.append(conflict)
            structure._conflict_ids.add(conflict.conflict_id)
        # Task 15: review history (backward compatible: missing key -> empty)
        from src.knowledge_review import ReviewRecord
        for rr_data in data.get('review_records', []):
            rr = ReviewRecord.from_dict(rr_data)
            structure.review_records.append(rr)
            if rr.review_id:
                structure._review_ids.add(rr.review_id)
        return structure
    def get_related_points(self, knowledge_id: str) -> list[str]:
        related = []
        for r in self.relationships:
            if r.source_id == knowledge_id:
                related.append(r.target_id)
            elif r.target_id == knowledge_id:
                related.append(r.source_id)
        return related
    def supporting_evidence_ids(self, knowledge_id: str) -> list[str]:
        """De-duplicated evidence IDs referenced by a KnowledgePoint, stable order."""
        kp = self.knowledge_points.get(knowledge_id)
        if kp is None:
            return []
        seen = set()
        out = []
        for ref in kp.evidence_refs:
            if ref and ref not in seen:
                seen.add(ref)
                out.append(ref)
        return out

    def conflict_count(self, knowledge_id: str) -> int:
        """Number of distinct conflicts (by stable conflict_id) touching a KnowledgePoint."""
        kp = self.knowledge_points.get(knowledge_id)
        if kp is None:
            return 0
        refs = set(kp.evidence_refs)
        seen = set()
        count = 0
        for c in self.conflicts:
            if not c.conflict_id or c.conflict_id in seen:
                continue
            if any(ref in refs for ref in c.evidence_refs):
                seen.add(c.conflict_id)
                count += 1
        return count

    def supporting_evidence_count(self, knowledge_id: str) -> int:
        """Count of unique Evidence IDs supporting a KnowledgePoint."""
        return len(self.supporting_evidence_ids(knowledge_id))
