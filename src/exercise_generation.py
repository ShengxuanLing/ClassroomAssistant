"""Deterministic, evidence-grounded exercise generation (Task 64).

What this module is
-------------------
A **template engine**, not an AI.  Given a ``KnowledgePoint`` and its
``Evidence``, it renders one of a small set of fixed question shapes
whose text is built *only* from strings that already exist in the
course.  Nothing is invented: every stem, option and reference answer
is either

- a verbatim substring of the knowledge point title / content, or
- a fixed template phrase, or
- a *structural* distractor (``None of the above``, a re-ordering of
  the same fact, ...) that asserts nothing new.

Why not an LLM
--------------
Spec 64.3 forbids it.  More importantly, an exercise's grounding chain
is ``Exercise -> KnowledgePoint -> Evidence -> Material``; a generator
that paraphrases can break that chain silently.  A template engine
cannot, because it has no way to emit a sentence it did not receive.

The grounding chain (spec 64.2 / 64.9)
--------------------------------------
``generate()`` returns an ``ExerciseDraft`` carrying

- ``knowledge_point_id``  — the single KP the item is about,
- ``evidence_ids``        — the KP's own evidence refs (never a
  neighbour's: spec 31's cross-KP rule is respected),
- ``grounding``           — a per-field provenance record saying which
  source string each rendered field came from, so the UI can show
  "why this question" without recomputing anything.

Determinism (spec 64.4)
-----------------------
The same ``(KnowledgePoint, template, config)`` MUST yield a byte-
identical draft.  Therefore:

- no ``uuid4``, no ``datetime.now``, no ``random``, no builtin
  ``hash()`` (PYTHONHASHSEED-dependent);
- ordering is explicit and total;
- the draft id is ``"draft-" + sha256(canonical payload)[:24]``.

A caller who genuinely wants variety can pass ``seed``; the seed only
selects *which* template is used, and it is part of the draft id, so
the same seed is reproducible forever.

Refusals (spec 64.10 / 64.12)
-----------------------------
- ``unverified`` knowledge -> ``GenerationRefusal(UNVERIFIED_KNOWLEDGE)``
  (recommended next step: review it first).
- ``conflicted`` knowledge -> ``GenerationRefusal(CONFLICTED_KNOWLEDGE)``
  (recommended next step: resolve the conflict).
- empty content / no usable sentence -> ``GenerationRefusal(NO_USABLE_STATEMENT)``.

An *optional practice* mode exists for unverified material
(spec 64.11): the caller must opt in explicitly *and* the resulting
exercise is marked ``basis="unverified_material"`` so the UI is
obliged to show "Based on unverified classroom material".
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Sequence, Tuple

__all__ = [
    "GENERATOR_VERSION",
    "DRAFT_SCHEMA_VERSION",
    "ExerciseBasis",
    "RefusalReason",
    "TemplateId",
    "GenerationRefusal",
    "ExerciseDraft",
    "GenerationConfig",
    "ExerciseTemplateGenerator",
    "UNVERIFIED_PRACTICE_BANNER",
    "STRUCTURAL_DISTRACTORS",
]


GENERATOR_VERSION = "template-v1"
DRAFT_SCHEMA_VERSION = 1

#: Shown verbatim by the UI whenever an exercise was generated from
#: material that has not been verified (spec 64.11).
UNVERIFIED_PRACTICE_BANNER = "Based on unverified classroom material"

#: Distractors that make a claim about nothing.  They are the *only*
#: options the generator may add that are not verbatim course text.
STRUCTURAL_DISTRACTORS: Tuple[str, ...] = (
    "None of the above / Cap de les anteriors",
    "Not stated in the material / No consta al material",
)


class ExerciseBasis(str, Enum):
    """Why an exercise was allowed to exist (spec 64.10 / 64.11)."""

    #: Formal exercise: the knowledge is ``supported`` and reviewable.
    FORMAL = "formal"
    #: Optional practice on material that has not been verified.
    UNVERIFIED_MATERIAL = "unverified_material"


class RefusalReason(str, Enum):
    """Stable reasons the generator declines to produce a draft."""

    UNVERIFIED_KNOWLEDGE = "unverified_knowledge"
    CONFLICTED_KNOWLEDGE = "conflicted_knowledge"
    NO_USABLE_STATEMENT = "no_usable_statement"
    MISSING_KNOWLEDGE_POINT = "missing_knowledge_point"


class TemplateId(str, Enum):
    """The fixed set of question shapes this engine can render."""

    #: "X is Y." -> True / False
    TRUE_FALSE_DEFINITION = "true_false_definition"
    #: Which of the following describes X? -> one verbatim option
    MULTIPLE_CHOICE_DEFINITION = "multiple_choice_definition"
    #: "What does X refer to?" -> the KP's own statement
    SHORT_ANSWER_DEFINITION = "short_answer_definition"
    #: Fill in the term omitted from the KP's own statement
    FILL_BLANK_TERM = "fill_blank_term"


#: Deterministic template preference order.  A generator tries each in
#: turn and keeps the first one whose preconditions hold, so the same
#: knowledge point always lands on the same shape.
TEMPLATE_ORDER: Tuple[TemplateId, ...] = (
    TemplateId.TRUE_FALSE_DEFINITION,
    TemplateId.MULTIPLE_CHOICE_DEFINITION,
    TemplateId.SHORT_ANSWER_DEFINITION,
    TemplateId.FILL_BLANK_TERM,
)

#: Which exercise type each template renders.
_TEMPLATE_EXERCISE_TYPE: Mapping[TemplateId, str] = {
    TemplateId.TRUE_FALSE_DEFINITION: "true_false",
    TemplateId.MULTIPLE_CHOICE_DEFINITION: "multiple_choice",
    TemplateId.SHORT_ANSWER_DEFINITION: "short_answer",
    TemplateId.FILL_BLANK_TERM: "fill_blank",
}

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
_WHITESPACE = re.compile(r"\s+")

#: Structural markdown / bullet prefixes.  These are layout, not
#: course facts, so they must never end up inside a question stem.
_STRUCTURAL_PREFIX = re.compile(r"^(?:#{1,6}\s*|[-*+•]\s+|\d+[.)]\s+|>\s*)+")

#: A "statement" must carry a verb-ish payload.  These markers are a
#: cheap, language-agnostic signal that a sentence says something
#: rather than merely naming a section.
_FACT_MARKERS = (
    " es ", " és ", " son ", " són ", " es:", " és:",
    " = ", ":", "->", "→",
    " un ", " una ", " el ", " la ", " els ", " les ",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha24(payload: Any) -> str:
    if isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = _canonical_json(payload).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _clean(text: Any) -> str:
    return _WHITESPACE.sub(" ", str(text or "")).strip()


def _strip_structural(text: str) -> str:
    """Remove markdown heading / bullet markers from a candidate line."""
    return _STRUCTURAL_PREFIX.sub("", text).strip()


def _is_askable(sentence: str) -> bool:
    """True when a sentence states something worth asking about.

    Rejects bare headings ("Tema 1", "# Resum Tema 1") because a
    heading cannot ground a true/false claim: asking "is this true?"
    about a section title would produce a question with no factual
    content, which is exactly what spec 64.6 forbids.
    """
    lowered = " " + sentence.lower() + " "
    if any(marker in lowered for marker in _FACT_MARKERS):
        return True
    # A sentence with several words is likely prose even without a
    # recognised marker; a 3-word heading is not.
    return len(sentence.split()) >= 6


def _sentences(*blocks: Any) -> Tuple[str, ...]:
    """Split blocks into clean, askable sentences, order preserved.

    Structural prefixes are stripped *before* the length test, and
    sentences that are only headings are dropped: they are layout
    ("Tema 1", "# Resum"), not facts, and a question built from one
    would assert nothing.
    """
    out: list[str] = []
    seen: set[str] = set()
    for block in blocks:
        for chunk in _SENTENCE_SPLIT.split(str(block or "")):
            sentence = _strip_structural(_clean(chunk))
            if len(sentence) < 8:
                continue
            if not _is_askable(sentence):
                continue
            if sentence in seen:
                continue
            seen.add(sentence)
            out.append(sentence)
    return tuple(out)


@dataclass(frozen=True)
class GenerationRefusal:
    """The generator's structured "no", with a recommended next step."""

    reason: RefusalReason
    knowledge_point_id: str
    detail: str
    recommended_action: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason.value,
            "knowledge_point_id": self.knowledge_point_id,
            "detail": self.detail,
            "recommended_action": self.recommended_action,
        }


class GenerationError(Exception):
    """Raised only by ``require``/strict helpers; normal flow returns a refusal."""


@dataclass(frozen=True)
class GenerationConfig:
    """Everything that influences a draft, and therefore its id.

    ``seed`` is deliberately *not* a random source: it is a stable
    index into the template preference list, so passing the same seed
    always reproduces the same question (spec 64.4).
    """

    template: Optional[TemplateId] = None
    seed: Optional[int] = None
    allow_unverified: bool = False
    distractor_pool: Tuple[str, ...] = STRUCTURAL_DISTRACTORS
    max_options: int = 4
    generator_version: str = GENERATOR_VERSION

    def __post_init__(self) -> None:
        if self.seed is not None:
            if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
                raise ValueError("seed must be a non-negative integer when provided")
        if self.max_options < 2:
            raise ValueError("max_options must be at least 2")
        if self.template is not None and not isinstance(self.template, TemplateId):
            raise ValueError(f"template must be a TemplateId, got {type(self.template).__name__}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template.value if self.template else None,
            "seed": self.seed,
            "allow_unverified": self.allow_unverified,
            "distractor_pool": list(self.distractor_pool),
            "max_options": self.max_options,
            "generator_version": self.generator_version,
        }


@dataclass(frozen=True)
class ExerciseDraft:
    """A generated (not yet persisted) exercise plus its grounding.

    ``grounding`` maps each rendered field to the source it came from.
    Every value is one of ``knowledge.title``, ``knowledge.content``,
    ``evidence:<id>`` or ``template:<phrase>`` — the UI can render this
    directly as "why this question", and a test can assert that no
    field claims a source that does not exist.
    """

    draft_id: str
    knowledge_point_id: str
    exercise_type: str
    template: TemplateId
    prompt: str
    choices: Tuple[Tuple[str, str], ...]
    correct_choice_id: Optional[str]
    expected_answer: Optional[str]
    blank_id: Optional[str]
    accepted_answers: Tuple[str, ...]
    explanation: Optional[str]
    evidence_ids: Tuple[str, ...]
    basis: ExerciseBasis
    grounding: Mapping[str, str] = field(default_factory=dict)
    config: Mapping[str, Any] = field(default_factory=dict)
    schema_version: int = DRAFT_SCHEMA_VERSION
    generator_version: str = GENERATOR_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id": self.draft_id,
            "knowledge_point_id": self.knowledge_point_id,
            "exercise_type": self.exercise_type,
            "template": self.template.value,
            "prompt": self.prompt,
            "choices": [{"choice_id": c, "text": t} for c, t in self.choices],            "correct_choice_id": self.correct_choice_id,
            "expected_answer": self.expected_answer,
            "blank_id": self.blank_id,
            "accepted_answers": list(self.accepted_answers),
            "explanation": self.explanation,
            "evidence_ids": list(self.evidence_ids),
            "basis": self.basis.value,
            "grounding": dict(sorted(self.grounding.items())),
            "config": dict(self.config),
            "schema_version": self.schema_version,
            "generator_version": self.generator_version,
        }


def _preferred_templates(config: GenerationConfig) -> Tuple[TemplateId, ...]:
    """Deterministic template order for this config."""
    if config.template is not None:
        return (config.template,)
    if config.seed is None:
        return TEMPLATE_ORDER
    # Rotate by seed.  Deterministic and total: same seed -> same order.
    offset = config.seed % len(TEMPLATE_ORDER)
    return TEMPLATE_ORDER[offset:] + TEMPLATE_ORDER[:offset]


class ExerciseTemplateGenerator:
    """Render one grounded exercise from one knowledge point.

    The class is stateless with respect to course data: it is handed a
    plain mapping (the KP DTO) plus a sequence of evidence DTOs and a
    callable that resolves an evidence id to the sentence(s) it
    supports.  That keeps it testable without a workspace and makes it
    impossible for it to reach around the application layer.
    """

    def __init__(self, *, version: str = GENERATOR_VERSION) -> None:
        self.version = version

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def can_generate(
        self,
        knowledge_point: Mapping[str, Any],
        *,
        config: Optional[GenerationConfig] = None,
    ) -> Optional[GenerationRefusal]:
        """``None`` when a draft is possible, else the structured refusal."""
        cfg = config or GenerationConfig()
        kp_id = str(knowledge_point.get("knowledge_id") or "")
        if not kp_id:
            return GenerationRefusal(
                RefusalReason.MISSING_KNOWLEDGE_POINT,
                "",
                "the knowledge point has no knowledge_id",
                "register the knowledge point first",
            )

        validation = str(knowledge_point.get("validation_status") or "unverified").lower()
        if validation == "conflicted":
            return GenerationRefusal(
                RefusalReason.CONFLICTED_KNOWLEDGE,
                kp_id,
                "conflicting evidence must be resolved before an exercise can be grounded",
                "resolve the conflict in the Review Center",
            )
        if validation != "supported" and not cfg.allow_unverified:
            return GenerationRefusal(
                RefusalReason.UNVERIFIED_KNOWLEDGE,
                kp_id,
                f"validation_status={validation!r} is not supported",
                "review the knowledge point first",
            )

        if not self._statement(knowledge_point):
            return GenerationRefusal(
                RefusalReason.NO_USABLE_STATEMENT,
                kp_id,
                "the knowledge point has no statement long enough to ask about",
                "add classroom material that states this knowledge point",
            )
        return None

    def generate(
        self,
        knowledge_point: Mapping[str, Any],
        evidence: Optional[Sequence[Mapping[str, Any]]] = None,
        *,
        config: Optional[GenerationConfig] = None,
    ) -> Any:
        """Return an ``ExerciseDraft`` or a ``GenerationRefusal``.

        Never raises for a legitimate domain situation: a refusal *is*
        a result (spec 64.10 / 64.12 say "do not generate", not "crash").
        """
        cfg = config or GenerationConfig()
        refusal = self.can_generate(knowledge_point, config=cfg)
        if refusal is not None:
            return refusal

        kp_id = str(knowledge_point.get("knowledge_id"))
        statement = self._statement(knowledge_point)
        evidence_ids = tuple(
            sorted(
                {
                    str(e.get("evidence_id"))
                    for e in (evidence or ())
                    if e and e.get("evidence_id")
                }
                | {
                    str(r)
                    for r in (knowledge_point.get("evidence_refs") or ())
                    if r
                }
            )
        )
        title = _clean(knowledge_point.get("title"))
        terms = tuple(
            sorted(
                {
                    cleaned
                    for cleaned in (
                        _strip_structural(_clean(t))
                        for t in (knowledge_point.get("original_terms") or ())
                    )
                    if cleaned
                }
            )
        )

        basis = (
            ExerciseBasis.FORMAL
            if str(knowledge_point.get("validation_status") or "").lower() == "supported"
            else ExerciseBasis.UNVERIFIED_MATERIAL
        )

        for template in _preferred_templates(cfg):
            built = self._render(template, knowledge_point, statement, title, terms, cfg)
            if built is None:
                continue
            payload = {
                "knowledge_point_id": kp_id,
                "template": template.value,
                "prompt": built["prompt"],
                "choices": built["choices"],
                "correct_choice_id": built["correct_choice_id"],
                "expected_answer": built["expected_answer"],
                "blank_id": built["blank_id"],
                "accepted_answers": built["accepted_answers"],
                "evidence_ids": list(evidence_ids),
                "basis": basis.value,
                "generator_version": cfg.generator_version,
                "schema_version": DRAFT_SCHEMA_VERSION,
            }
            return ExerciseDraft(
                draft_id="draft-" + _sha24(payload),
                knowledge_point_id=kp_id,
                exercise_type=_TEMPLATE_EXERCISE_TYPE[template],
                template=template,
                prompt=built["prompt"],
                choices=tuple(built["choices"]),
                correct_choice_id=built["correct_choice_id"],
                expected_answer=built["expected_answer"],
                blank_id=built["blank_id"],
                accepted_answers=tuple(built["accepted_answers"]),
                explanation=built["explanation"],
                evidence_ids=evidence_ids,
                basis=basis,
                grounding=built["grounding"],
                config=cfg.to_dict(),
            )

        # Every template declined even though can_generate passed: the
        # only way here is a statement with no askable term.
        return GenerationRefusal(
            RefusalReason.NO_USABLE_STATEMENT,
            kp_id,
            "no template could render this knowledge point",
            "add classroom material that states this knowledge point",
        )

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _statement(self, kp: Mapping[str, Any]) -> str:
        """The single best sentence to ask about, or ``""``.

        Preference is content-first because the title is often a
        heading; a heading alone cannot ground a true/false claim.
        """
        sentences = _sentences(kp.get("content"), kp.get("title"))
        return sentences[0] if sentences else ""

    def _render(
        self,
        template: TemplateId,
        kp: Mapping[str, Any],
        statement: str,
        title: str,
        terms: Tuple[str, ...],
        cfg: GenerationConfig,
    ) -> Optional[dict[str, Any]]:
        if template is TemplateId.TRUE_FALSE_DEFINITION:
            return self._true_false(statement, cfg)
        if template is TemplateId.MULTIPLE_CHOICE_DEFINITION:
            return self._multiple_choice(statement, terms, cfg)
        if template is TemplateId.SHORT_ANSWER_DEFINITION:
            return self._short_answer(statement, cfg)
        if template is TemplateId.FILL_BLANK_TERM:
            return self._fill_blank(statement, terms, cfg)
        return None

    def _true_false(
        self, statement: str, cfg: GenerationConfig
    ) -> Optional[dict[str, Any]]:
        """The statement itself, verbatim, judged true/false.

        Spec 64.6: the answer must be *directly* supported by the
        knowledge, so the correct answer is always "true" and the stem
        is never altered.  We do not flip a fact into a false claim,
        because that would require inventing a falsehood.
        """
        prompt = statement + " — True / False?"
        return {
            "prompt": prompt,
            "choices": [
                ("true", "Vero / True"),
                ("false", "Falso / False"),
            ],
            "correct_choice_id": "true",
            "expected_answer": None,
            "blank_id": None,
            "accepted_answers": [],
            "explanation": "The statement is quoted verbatim from the classroom material.",
            "grounding": {
                "prompt": "knowledge.content",
                "correct_choice_id": "knowledge.content",
            },
        }

    def _multiple_choice(
        self, statement: str, terms: Tuple[str, ...], cfg: GenerationConfig
    ) -> Optional[dict[str, Any]]:
        """One verbatim option plus structural distractors (spec 64.7).

        Deliberately NOT a set of plausible-but-wrong course facts:
        the engine has no way to know which neighbour facts are wrong,
        so any such option would be a fabricated claim.
        """
        options: list[tuple[str, str]] = [("a", statement)]
        for index, distractor in enumerate(cfg.distractor_pool):
            if len(options) >= cfg.max_options:
                break
            options.append((chr(ord("b") + index), distractor))
        if len(options) < 2:
            return None
        return {
            "prompt": "Which statement matches the classroom material?",
            "choices": options,
            "correct_choice_id": "a",
            "expected_answer": None,
            "blank_id": None,
            "accepted_answers": [],
            "explanation": "Only one option appears verbatim in the material.",
            "grounding": {
                "prompt": "template:multiple_choice_definition",
                "choices.a": "knowledge.content",
                "choices.b+": "template:structural_distractor",
                "correct_choice_id": "knowledge.content",
            },
        }

    def _short_answer(
        self, statement: str, cfg: GenerationConfig
    ) -> Optional[dict[str, Any]]:
        """Ask for the statement; the expected answer IS the statement.

        Spec 64.8: grading stays with the existing evaluation layer —
        this engine only supplies the reference text.
        """
        return {
            "prompt": "State the knowledge point in your own words:",
            "choices": [],
            "correct_choice_id": None,
            "expected_answer": statement,
            "blank_id": None,
            "accepted_answers": [],
            "explanation": "Compared verbatim against the material by the existing evaluator.",
            "grounding": {
                "prompt": "template:short_answer_definition",
                "expected_answer": "knowledge.content",
            },
        }

    def _fill_blank(
        self, statement: str, terms: Tuple[str, ...], cfg: GenerationConfig
    ) -> Optional[dict[str, Any]]:
        """Blank out a term that literally occurs in the statement.

        Requires an ``original_terms`` entry (usually the Spanish or
        Catalan source term).  Without one there is nothing that can be
        removed without rewriting the sentence, so the template declines.
        """
        blanked: Optional[Tuple[str, str]] = None
        for term in terms:
            if not term or term not in statement:
                continue
            prompt = statement.replace(term, "____", 1)
            # 挖空后必须还剩"别的字", 否则这道题就只是 "____",
            # 既没有语境也没有可判断的信息量。原样放弃这个模板。
            if len(_clean(prompt)) - len("____") < 8:
                continue
            blanked = (term, prompt)
            break
        if blanked is None:
            return None
        term, prompt = blanked
        return {
            "prompt": prompt,
            "choices": [],
            "correct_choice_id": None,
            "expected_answer": None,
            "blank_id": "b1",
            "accepted_answers": [term],
            "explanation": "The blank is filled with the term used in the material.",
            "grounding": {
                "prompt": "knowledge.content",
                "accepted_answers.0": "knowledge.original_terms",
            },
        }


def generate_for_knowledge_points(
    generator: ExerciseTemplateGenerator,
    knowledge_points: Iterable[Mapping[str, Any]],
    evidence_by_kp: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    config: Optional[GenerationConfig] = None,
) -> list[Any]:
    """Generate for a deterministic, sorted batch of knowledge points.

    Ordering is by ``knowledge_id`` so a batch is reproducible and so
    the caller never depends on dict iteration order.
    """
    out: list[Any] = []
    for kp in sorted(knowledge_points, key=lambda k: str(k.get("knowledge_id") or "")):
        kp_id = str(kp.get("knowledge_id") or "")
        out.append(generator.generate(kp, evidence_by_kp.get(kp_id, ()), config=config))
    return out
