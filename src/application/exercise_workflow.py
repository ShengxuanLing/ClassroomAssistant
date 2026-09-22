"""Exercise Workflow (Task 64): generation -> grounding -> answer -> evaluation.

This is the **assembly layer** between the template engine
(:mod:`src.exercise_generation`) and the existing learning layer
(:class:`src.application.learning_service.LearningService`).  It owns
exactly four responsibilities and nothing else:

1. **Read** the course's knowledge points and their evidence through
   the existing services (never re-derive facts).
2. **Generate** a deterministic draft, refusing on
   ``unverified`` / ``conflicted`` knowledge (spec 64.10 / 64.12).
3. **Persist** the draft as a real exercise through
   ``LearningService.create_exercise``, which is already
   content-addressed and therefore idempotent (spec 64.13) — the same
   draft always yields the same ``exercise_id``, so regenerating a
   batch cannot inflate the exercise table.
4. **Report** the full grounding chain
   ``Exercise -> KnowledgePoint -> Evidence -> Material`` (spec 64.2)
   by joining existing DTOs; nothing is stored twice.

What this module deliberately does NOT do
-----------------------------------------
- It never writes a knowledge point, review record or student state.
- It never grades: ``submit_answer`` delegates to the existing
  evaluator (spec 64.8 / 64.17).
- It never mutates ``StudentState`` directly: evaluation may trigger
  the Task 30 state machine inside the domain layer (spec 64.19).
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from src.application.errors import InvalidInputError, NotFoundError
from src.exercise_generation import (
    UNVERIFIED_PRACTICE_BANNER,
    ExerciseBasis,
    ExerciseTemplateGenerator,
    GenerationConfig,
    GenerationRefusal,
    TemplateId,
)

__all__ = ["ExerciseWorkflow", "WORKFLOW_VERSION"]

WORKFLOW_VERSION = "exercise-workflow-v1"

#: Fields carried from the KP so the UI can render its "why" panel
#: without a second round trip.
_KP_FIELDS = (
    "knowledge_id",
    "title",
    "validation_status",
    "review_status",
    "knowledge_score",
    "needs_verification",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


class ExerciseWorkflow:
    """Deterministic, evidence-grounded exercise workflow for one workspace."""

    def __init__(
        self,
        workspace: Any,
        *,
        generator: Optional[ExerciseTemplateGenerator] = None,
    ) -> None:
        self._ws = workspace
        self._generator = generator or ExerciseTemplateGenerator()

    # ------------------------------------------------------------------
    # Preview (纯读; 不落库)
    # ------------------------------------------------------------------

    def preview(
        self,
        course_id: str,
        knowledge_point_id: str,
        *,
        config: Optional[GenerationConfig] = None,
    ) -> dict[str, Any]:
        """Generate a draft without persisting anything.

        Useful for the UI's "generate" button and for tests that must
        prove generation is read-only.
        """
        cid = _clean(course_id)
        kp_id = _clean(knowledge_point_id)
        if not cid:
            raise InvalidInputError("course_id must be non-empty")
        if not kp_id:
            raise InvalidInputError("knowledge_point_id must be non-empty")

        kp = self._knowledge_point(cid, kp_id)
        cfg = config or GenerationConfig()
        evidence = self._ws.knowledge_evidence(cid, kp_id)
        draft = self._generator.generate(kp, evidence, config=cfg)
        if isinstance(draft, GenerationRefusal):
            return {
                "course_id": cid,
                "generated": False,
                "refusal": draft.to_dict(),
                "grounding": None,
            }
        draft_dict = draft.to_dict()
        return {
            "course_id": cid,
            "generated": True,
            "refusal": None,
            "draft": draft_dict,
            "grounding": self._grounding_chain(cid, draft_dict),
        }

    # ------------------------------------------------------------------
    # Generate + persist (幂等)
    # ------------------------------------------------------------------

    def generate(
        self,
        course_id: str,
        knowledge_point_id: str,
        *,
        config: Optional[GenerationConfig] = None,
    ) -> dict[str, Any]:
        """Preview, then persist through the existing exercise layer.

        Idempotency (spec 64.13) is *inherited*, not re-implemented:
        ``Exercise.create`` derives ``exercise_id`` from the canonical
        content, and ``LearningService.create_exercise`` returns the
        existing record when that id is already present.  Generating
        the same draft twice therefore creates exactly one exercise.
        """
        cid = _clean(course_id)
        preview = self.preview(cid, knowledge_point_id, config=config)
        if not preview["generated"]:
            return preview

        draft = preview["draft"]
        existing_ids = {
            str(e.get("exercise_id")) for e in self._ws.list_exercises(cid)
        }
        exercise = self._create_from_draft(cid, draft)
        return {
            **preview,
            "exercise": exercise,
            "exercise_id": exercise.get("exercise_id"),
            "created": str(exercise.get("exercise_id")) not in existing_ids,
            "grounding": self._grounding_chain(cid, draft),
            "banner": (
                UNVERIFIED_PRACTICE_BANNER
                if draft.get("basis") == ExerciseBasis.UNVERIFIED_MATERIAL.value
                else None
            ),
        }

    def generate_batch(
        self,
        course_id: str,
        *,
        knowledge_point_ids: Optional[Sequence[str]] = None,
        config: Optional[GenerationConfig] = None,
        limit: Optional[int] = None,
    ) -> dict[str, Any]:
        """Generate for a deterministic set of knowledge points.

        Sorted by ``knowledge_id``; ``limit`` truncates *after*
        sorting so the same limit always selects the same subset.
        """
        cid = _clean(course_id)
        if not cid:
            raise InvalidInputError("course_id must be non-empty")

        if knowledge_point_ids:
            ids = sorted({_clean(k) for k in knowledge_point_ids if _clean(k)})
        else:
            ids = sorted(
                str(kp.get("knowledge_id"))
                for kp in self._ws.knowledge_points(cid)
            )
        if limit is not None:
            ids = ids[: max(0, int(limit))]

        results: list[dict[str, Any]] = []
        refusals: list[dict[str, Any]] = []
        created = 0
        for kp_id in ids:
            row = self.generate(cid, kp_id, config=config)
            if row["generated"]:
                created += 1 if row.get("created") else 0
                results.append(row)
            else:
                refusals.append({"knowledge_point_id": kp_id, **row["refusal"]})
        return {
            "course_id": cid,
            "requested": len(ids),
            "generated": len(results),
            "created": created,
            "reused": len(results) - created,
            "refused": len(refusals),
            "items": results,
            "refusals": refusals,
        }

    # ------------------------------------------------------------------
    # Grounding chain (spec 64.2 / 64.9)
    # ------------------------------------------------------------------

    def grounding(
        self, course_id: str, exercise_id: str
    ) -> dict[str, Any]:
        """Full ``Exercise -> KP -> Evidence -> Material`` chain.

        Every hop is resolved from an existing store.  A hop that
        cannot be resolved is reported as ``None`` / listed in
        ``unresolved`` rather than silently dropped — the UI must be
        able to say "this reference is broken" instead of pretending
        the chain is complete.
        """
        cid = _clean(course_id)
        eid = _clean(exercise_id)
        if not cid:
            raise InvalidInputError("course_id must be non-empty")
        if not eid:
            raise InvalidInputError("exercise_id must be non-empty")

        exercise = self._ws.get_exercise(cid, eid)
        return self._grounding_chain(cid, exercise)

    def evidence_trace(self, course_id: str, knowledge_point_id: str) -> dict[str, Any]:
        """``KnowledgePoint -> Evidence -> Material`` (used by the UI too)."""
        cid = _clean(course_id)
        kp_id = _clean(knowledge_point_id)
        kp = self._knowledge_point(cid, kp_id)
        return {
            "course_id": cid,
            "knowledge_point": {k: kp.get(k) for k in _KP_FIELDS},
            "evidence": self._evidence_chain(cid, kp_id),
        }

    # ------------------------------------------------------------------
    # Answer -> Evaluation (纯委托)
    # ------------------------------------------------------------------

    def start(self, course_id: str, exercise_id: str) -> dict[str, Any]:
        """What the UI needs to render "start exercise" without the answer.

        Returns the exercise view plus the grounding chain, with
        ``answer_withheld`` explicitly true so the UI contract is
        auditable rather than implied.
        """
        cid = _clean(course_id)
        exercise = self._ws.get_exercise(cid, _clean(exercise_id))
        return {
            "course_id": cid,
            "exercise": exercise,
            "grounding": self._grounding_chain(cid, exercise),
            "answer_withheld": True,
        }

    def submit(
        self,
        course_id: str,
        student_id: str,
        exercise_id: str,
        submitted_value: str,
        sequence: int = 0,
    ) -> dict[str, Any]:
        """Submit and return the evaluation produced by the existing layer.

        Deliberately thin: the workflow adds grounding information to
        the response so the student can see *why* the answer is what it
        is, but the correctness verdict comes only from
        ``LearningService.submit_answer``.
        """
        cid = _clean(course_id)
        eid = _clean(exercise_id)
        answer = self._ws.submit_answer(
            cid, _clean(student_id), eid, submitted_value, sequence
        )
        evaluation = None
        answer_id = answer.get("answer_id")
        if answer_id:
            try:
                evaluation = self._ws.get_evaluation(cid, str(answer_id))
            except NotFoundError:
                evaluation = None
        grounding: dict[str, Any]
        try:
            grounding = self._grounding_chain(cid, self._ws.get_exercise(cid, eid))
        except NotFoundError:
            grounding = {
                "course_id": cid,
                "knowledge_points": [],
                "unknown_knowledge_point_ids": [],
                "evidence": [],
                "materials": [],
                "unresolved": [f"exercise:{eid}"],
                "complete": False,
            }
        return {
            "course_id": cid,
            "student_id": student_id,
            "exercise_id": exercise_id,
            "answer": answer,
            "evaluation": evaluation,
            "grounding": grounding,
        }

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _knowledge_point(self, course_id: str, knowledge_point_id: str) -> dict[str, Any]:
        for kp in self._ws.knowledge_points(course_id):
            if str(kp.get("knowledge_id")) == knowledge_point_id:
                return kp
        raise NotFoundError(
            f"knowledge point {knowledge_point_id!r} not found in course {course_id!r}"
        )

    def _create_from_draft(self, course_id: str, draft: Mapping[str, Any]) -> dict[str, Any]:
        """Hand the draft to the existing exercise layer.

        Only real ``Exercise`` fields are forwarded; the grounding
        metadata stays in the draft (persisting it would duplicate
        information that is already derivable from the KP).
        """
        kwargs: dict[str, Any] = {
            "evidence_ids": list(draft.get("evidence_ids") or ()),
            "explanation": draft.get("explanation"),
        }
        exercise_type = str(draft.get("exercise_type"))
        if exercise_type == "true_false":
            kwargs["is_true"] = draft.get("correct_choice_id") == "true"
        elif exercise_type == "multiple_choice":
            kwargs["choices"] = list(draft.get("choices") or ())
            kwargs["correct_choice_id"] = draft.get("correct_choice_id")
        elif exercise_type == "short_answer":
            kwargs["expected_answer"] = draft.get("expected_answer")
        elif exercise_type == "fill_blank":
            kwargs["blank_id"] = draft.get("blank_id")
            kwargs["accepted_answers"] = list(draft.get("accepted_answers") or ())
        else:
            raise InvalidInputError(f"unsupported generated exercise_type: {exercise_type!r}")

        return self._ws.create_exercise(
            course_id,
            exercise_type,
            str(draft.get("prompt")),
            [str(draft.get("knowledge_point_id"))],
            **kwargs,
        )

    def _grounding_chain(
        self, course_id: str, source: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Join the chain for an exercise- or draft-shaped mapping."""
        kp_ids = [
            str(k)
            for k in (source.get("knowledge_point_ids") or ())
            if str(k)
        ]
        if not kp_ids and source.get("knowledge_point_id"):
            kp_ids = [str(source["knowledge_point_id"])]
        kp_ids = sorted(set(kp_ids))

        known: list[dict[str, Any]] = []
        unknown: list[str] = []
        evidence: list[dict[str, Any]] = []
        materials: dict[str, dict[str, Any]] = {}
        unresolved: list[str] = []

        declared_evidence = {
            str(e) for e in (source.get("evidence_ids") or ()) if e
        }

        for kp_id in kp_ids:
            try:
                kp = self._knowledge_point(course_id, kp_id)
            except NotFoundError:
                unknown.append(kp_id)
                unresolved.append(f"knowledge_point:{kp_id}")
                continue
            known.append({k: kp.get(k) for k in _KP_FIELDS})
            for row in self._evidence_chain(course_id, kp_id):
                evidence.append(row)
                material = row.get("material")
                if material and material.get("material_id"):
                    materials[str(material["material_id"])] = material

        evidence_ids = {str(r.get("evidence_id")) for r in evidence}
        for eid in sorted(declared_evidence - evidence_ids):
            # An exercise may cite evidence that its KP no longer
            # resolves (retired material).  Report it, never hide it.
            unresolved.append(f"evidence:{eid}")

        return {
            "course_id": course_id,
            "knowledge_points": known,
            "unknown_knowledge_point_ids": unknown,
            "evidence": evidence,
            "materials": [materials[k] for k in sorted(materials)],
            "unresolved": unresolved,
            "complete": not unresolved and bool(known) and bool(evidence),
            # Provenance of the *generation*, when the caller is looking
            # at a generated draft/exercise.  ``None`` for hand-authored
            # exercises — the UI must not claim template provenance for
            # something a teacher wrote.
            "generator_version": source.get("generator_version"),
            "template": source.get("template"),
        }

    def _evidence_chain(self, course_id: str, knowledge_point_id: str) -> list[dict[str, Any]]:
        """Evidence rows + the material each one came from."""
        out: list[dict[str, Any]] = []
        try:
            rows = self._ws.knowledge_evidence(course_id, knowledge_point_id)
        except (NotFoundError, InvalidInputError):
            return out
        for row in rows:
            source = row.get("source") or {}
            material_id = str(source.get("material_id") or "")
            material = None
            if material_id:
                material = self._material_brief(course_id, material_id)
            out.append(
                {
                    "evidence_id": row.get("evidence_id"),
                    "evidence_type": row.get("evidence_type"),
                    "language": row.get("language"),
                    "confidence": row.get("confidence"),
                    "content": row.get("content"),
                    "source": source,
                    "material": material,
                }
            )
        out.sort(key=lambda r: str(r.get("evidence_id") or ""))
        return out

    def _material_brief(self, course_id: str, material_id: str) -> Optional[dict[str, Any]]:
        try:
            row = self._ws.get_material(course_id, material_id)
        except (NotFoundError, InvalidInputError):
            return None
        if not isinstance(row, Mapping):
            return None
        return {
            "material_id": row.get("material_id"),
            "filename": row.get("filename"),
            "material_type": row.get("material_type"),
            "language": row.get("language"),
        }
