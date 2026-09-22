from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import List, Optional, Set

from src.knowledge_structure import (
    KnowledgeStructure,
    KnowledgePoint,
    Relationship,
    RelationType,
)
from src.models import (
    ClassSession,
    Evidence,
    EvidenceType,
    Language,
    Material,
)
from src.processor import ClassSessionProcessor
from src.integration import EvidenceIntegrator, EvidenceIntegrationResult
from src.knowledge_validation import KnowledgeValidator


def _stable_knowledge_id(*parts: str) -> str:
    payload = "||".join(parts).encode("utf-8")
    return "kp-" + hashlib.sha256(payload).hexdigest()[:16]


def _entity_anchor(evidence: Evidence) -> str:
    content = (evidence.content or "").strip()
    if not content:
        return ""
    first = content.split("\n", 1)[0]
    for sep in (". ", "; ", ": ", "¡", "¿"):
        idx = first.find(sep)
        if idx > 0:
            first = first[: idx + len(sep) - (1 if sep.endswith(" ") else 0)]
            break
    return first.strip().lower()


def _language_label(ev: Evidence) -> str:
    try:
        return ev.language.value
    except AttributeError:
        return "Unknown"


class KnowledgeExtractor:
    """Deterministic Evidence -> KnowledgePoint extraction."""

    @staticmethod
    def _confidence_rank(value: str) -> int:
        return {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "UNCERTAIN": 0}.get(value, 0)

    @classmethod
    def extract(
        cls,
        evidences: List[Evidence],
        integration: Optional[EvidenceIntegrationResult] = None,
    ) -> List[KnowledgePoint]:
        clusters: List[List[Evidence]] = []
        seen: Set[str] = set()
        if integration is not None:
            # Supporting groups: evidence with known semantic relationship
            # lands in a single KnowledgePoint.
            for sg in integration.supporting_groups:
                members = [
                    ev for ev in integration.evidence
                    if ev.evidence_id in sg.evidence_ids and ev.evidence_id not in seen
                ]
                if members:
                    seen.update(m.evidence_id for m in members)
                    clusters.append(members)
            # Remaining evidence forms single-item clusters.
            for ev in integration.evidence:
                if ev.evidence_id not in seen:
                    clusters.append([ev])
        else:
            for ev in evidences:
                clusters.append([ev])

        # Deduplicate clusters by content anchor + evidence type:
        # if two clusters share the same anchor (e.g. a supporting group
        # whose members each form their own cluster elsewhere), keep the
        # first (most-specific) cluster and drop later duplicates.
        deduped: List[List[Evidence]] = []
        anchor_seen: Set[str] = set()
        for cluster in clusters:
            primary = cluster[0]
            anchor = _entity_anchor(primary) + "\x00" + primary.evidence_type.value
            if anchor not in anchor_seen:
                anchor_seen.add(anchor)
                deduped.append(cluster)
        clusters = deduped

        points: List[KnowledgePoint] = []
        for cluster in clusters:
            primary = cluster[0]
            anchor = _entity_anchor(primary)
            weakest = min(cls._confidence_rank(e.confidence.value) for e in cluster)
            confidence = {3: "HIGH", 2: "MEDIUM", 1: "LOW", 0: "UNCERTAIN"}[weakest]

            content_lines: List[str] = []
            terms: List[str] = []
            for e in cluster:
                body = (e.content or "").strip()
                if body:
                    if body not in content_lines:
                        content_lines.append(body)
                    if body not in terms:
                        terms.append(body)

            evidence_refs = sorted(e.evidence_id for e in cluster)
            knowledge_id = _stable_knowledge_id(anchor, primary.evidence_type.value)

            kp = KnowledgePoint(
                knowledge_id=knowledge_id,
                title=(primary.content or "").strip().split("\n")[0][:80],
                content="\n".join(content_lines),
                original_terms=terms,
                importance="high"
                if primary.evidence_type in (EvidenceType.TEACHER_STATEMENT, EvidenceType.TRANSCRIPT)
                else "medium",
                confidence=confidence,
                evidence_refs=evidence_refs,
                related_points=[],
                needs_verification=confidence in ("LOW", "UNCERTAIN"),
            )
            points.append(kp)

        points.sort(key=lambda kp: kp.knowledge_id)
        return points


@dataclass
class PipelineResult:
    evidence_ids: List[str] = field(default_factory=list)
    new_knowledge_point_ids: List[str] = field(default_factory=list)
    updated_knowledge_point_ids: List[str] = field(default_factory=list)
    new_relationship_ids: List[str] = field(default_factory=list)
    conflict_ids: List[str] = field(default_factory=list)
    structure: Optional[KnowledgeStructure] = None
    conflicts: Optional[object] = field(default=None, compare=False, repr=False)


class KnowledgePipeline:
    """Orchestrates Evidence -> Integration -> Extraction -> KnowledgeStructure."""

    def __init__(
        self,
        integrator: Optional[type] = None,
        processor: Optional[ClassSessionProcessor] = None,
    ) -> None:
        self._integrator = integrator or EvidenceIntegrator
        self._processor = processor or ClassSessionProcessor()

    def process_evidence(
        self,
        evidence: List[Evidence],
        structure: Optional[KnowledgeStructure] = None,
    ) -> PipelineResult:
        if structure is None:
            structure = KnowledgeStructure()
        result = PipelineResult(structure=structure)
        if not evidence:
            return result

        integration = self._integrator.integrate(list(evidence))
        result.evidence_ids = [e.evidence_id for e in integration.evidence]
        result.conflict_ids = [c.conflict_id for c in integration.conflicts]
        result.conflicts = integration

        # Register conflict records into the structure (idempotent).
        for c in integration.conflicts:
            structure.add_conflict(c)

        conflict_refs: Set[str] = set()
        for c in integration.conflicts:
            conflict_refs.update(c.evidence_refs)

        for kp in KnowledgeExtractor.extract(integration.evidence, integration):
            if conflict_refs and (conflict_refs & set(kp.evidence_refs)):
                kp.needs_verification = True
            if kp.knowledge_id in structure.knowledge_points:
                result.updated_knowledge_point_ids.append(kp.knowledge_id)
                existing = structure.knowledge_points[kp.knowledge_id]
                merged = sorted(set(existing.evidence_refs) | set(kp.evidence_refs))
                if merged != existing.evidence_refs:
                    existing.evidence_refs = merged
                if kp.needs_verification:
                    existing.needs_verification = True
            else:
                structure.add_knowledge_point(kp)
                result.new_knowledge_point_ids.append(kp.knowledge_id)

        for sg in integration.supporting_groups:
            if len(sg.evidence_ids) < 2:
                continue
            kp_ids: Set[str] = set()
            for kp in structure.knowledge_points.values():
                if set(sg.evidence_ids) & set(kp.evidence_refs):
                    kp_ids.add(kp.knowledge_id)
            kp_ids = sorted(kp_ids)
            if len(kp_ids) < 2:
                continue
            a, b = kp_ids[0], kp_ids[1]
            rel = Relationship(
                source_id=a,
                target_id=b,
                relation_type=RelationType.RELATED_TO,
                evidence_refs=sorted(sg.evidence_ids),
            )
            if structure.add_relationship(rel):
                result.new_relationship_ids.append(rel.relationship_id)

        self._apply_validation(structure, evidence, integration)

        return result

    def _apply_validation(
        self,
        structure: KnowledgeStructure,
        evidence: List[Evidence],
        conflicts: Optional[object] = None,
    ) -> None:
        """Apply read-only validation to the structure (Task 14).

        Recomputes validation_status / knowledge_score / needs_verification
        for every KnowledgePoint from the current Evidence base and the
        structure's conflicts. The structure's relationships and conflict
        records are left untouched.
        """
        results = KnowledgeValidator.validate_structure(structure, evidence, conflicts)
        for kp in structure.knowledge_points.values():
            r = results.get(kp.knowledge_id)
            if r is None:
                continue
            kp.validation_status = r.status.value
            kp.knowledge_score = r.knowledge_score
            kp.needs_verification = r.needs_verification

    def process_session(
        self,
        session: ClassSession,
        materials: List[Material],
        structure: Optional[KnowledgeStructure] = None,
    ) -> PipelineResult:
        evidence = self._processor.process_session(session, materials)
        return self.process_evidence(evidence, structure=structure)