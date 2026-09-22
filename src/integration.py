"""Multi-source Evidence Integration module for the Classroom Assistant project.

This module implements the first layer of multi-source information integration.
It accepts Evidence[] from multiple sources and produces an organized result
that identifies duplicates, supporting relationships, and conflicts.

Core principles:
- Original Evidence is never modified or deleted
- Deterministic comparison only (no AI/embedding/semantic matching)
- Language-agnostic text normalization for comparison
- All relationships explicitly categorized as DUPLICATE, SUPPORTING, CONFLICT, or UNKNOWN
- Results are stable across repeated runs
"""

from __future__ import annotations
import unicodedata
import re
import uuid
import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from src.models import Evidence, SourceReference, VerificationStatus


class RelationshipType(str, Enum):
    UNKNOWN = "UNKNOWN"
    DUPLICATE = "DUPLICATE"
    SUPPORTING = "SUPPORTING"
    CONFLICT = "CONFLICT"


@dataclass
class DuplicateGroup:
    evidence_ids: list[str] = field(default_factory=list)
    contents: list[str] = field(default_factory=list)


@dataclass
class SupportingGroup:
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class ConflictRecord:
    conflict_id: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    description: str = ""
    status: VerificationStatus = VerificationStatus.PENDING

    def __post_init__(self) -> None:
        if not self.conflict_id:
            self.conflict_id = str(uuid.uuid4())
        if isinstance(self.status, str):
            self.status = VerificationStatus.from_string(self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflict_id": self.conflict_id,
            "evidence_refs": self.evidence_refs,
            "description": self.description,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ConflictRecord":
        return cls(
            conflict_id=data.get("conflict_id", ""),
            evidence_refs=data.get("evidence_refs", []),
            description=data.get("description", ""),
            status=data.get("status", VerificationStatus.PENDING),
        )


@dataclass
class EvidenceIntegrationResult:
    evidence: list[Evidence] = field(default_factory=list)
    duplicate_groups: list[DuplicateGroup] = field(default_factory=list)
    supporting_groups: list[SupportingGroup] = field(default_factory=list)
    conflicts: list[ConflictRecord] = field(default_factory=list)
    conflict_refs_by_id: dict[str, frozenset[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence": [e.to_dict() for e in self.evidence],
            "duplicate_groups": [
                {"evidence_ids": dg.evidence_ids, "contents": dg.contents}
                for dg in self.duplicate_groups
            ],
            "supporting_groups": [
                {"evidence_ids": sg.evidence_ids}
                for sg in self.supporting_groups
            ],
            "conflicts": [c.to_dict() for c in self.conflicts],
        }


#: 否定标记词 (与 Task 6 原有集合完全一致, 只是提成常量避免两处漂移)。
_NEGATION_WORDS: list[str] = ["no es", "not", "non", "nicht", "no"]

#: 顺序关系模式 (与 Task 6 原有模式完全一致)。
_ORDER_PATTERNS: list[str] = [
    r"(.+?)\s*(?:antes\s+que|antes\s+de|previo\s+a|before)\s+(.+)",
    r"(.+?)\s*(?:despu[e\u0301\u0074]s\s+de|despues\s+de|after)\s+(.+)",
]


def _normalize_statements(text: str) -> str:
    """归一化, 但**保留换行** —— 语句边界是换行, 不是空格。

    Task 45 修复的真实缺陷
    ----------------------
    顺序关系的实体是 "『X』antes que『Y』" 里的 X 和 Y, 它们只在一句话内部
    成立。而 :meth:`EvidenceIntegrator.normalize_text` 会把所有空白 (含换行)
    压成单个空格 —— 于是一段多行证据被拼成**一行**, 正则里的 ``.+?`` 从证据
    开头开始吞, 实体变成"半篇讲稿"。

    真实课堂数据把这条放大到了必现: 一次课堂录音是一整条转写证据 (6 句话,
    含教师对 Dijkstra 两步先后顺序的原话), 归一化后整段成为一行, 左实体
    变成前 4 句话的拼接, 右实体变成后 2 句话的拼接 —— 于是它与学生笔记里
    明确写反的顺序**匹配不上**, 真实的冲突被静默丢掉。

    这里只改**语句切分**, 不改用于去重 / 实体比较的 :meth:`normalize_text`:
    后者压平空白是它的既定契约 (去重需要 "a  b" == "a b")。
    """
    normalized = unicodedata.normalize("NFKC", text)
    lines = [
        re.sub(r"[ \t]+", " ", line).strip()
        for line in normalized.splitlines()
    ]
    return "\n".join(line for line in lines if line).lower()


def _negation_pattern(word: str) -> str:
    """Whole-word regex for one negation marker.

    Task 45 修复的真实缺陷
    ----------------------
    旧实现是 ``r"\\b\\w*\\s*" + word + r"\\s*\\w*"``: 它允许否定词出现在
    **任意单词内部**。对 "no" 这类短词后果严重 —— 西班牙语 "lumi**no**sa"
    (发光) / "co**no**cido" 会被判成否定语句, 于是两段毫不矛盾的课堂笔记
    被报成 "Negation conflict", 知识点被标成 CONFLICTED 并推入人工复核队列。

    真实的课堂数据 (Task 45 验收数据集) 立刻暴露了这一点:
    "la fotosíntesis ... la energía luminosa en energía química" 与
    "- fase luminosa: ocurre en la membrana del tilacoide" 被报成冲突。

    修复: 否定词必须是**独立词** (两侧都不能紧邻单词字符)。
    语义上这正是 "negated statement" 的定义, 而不再是 "包含某个字母序列"。
    """
    return r"(?<!\w)" + re.escape(word) + r"(?!\w)"


class EvidenceIntegrator:
    """Integrates multiple Evidence objects, identifying duplicates, conflicts, and relationships.

    All comparisons use text normalization that does NOT modify the original Evidence content.
    Original Evidence objects and their SourceReferences are always preserved.
    """

    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize text for comparison purposes only.

        Does NOT modify the original evidence content.
        Applies Unicode normalization, whitespace collapse, and lowercase.
        """
        normalized = unicodedata.normalize("NFKC", text)
        normalized = re.sub(r"\s+", " ", normalized.strip())
        return normalized.lower()

    @staticmethod
    def integrate(evidence_list: list[Evidence]) -> EvidenceIntegrationResult:
        """Main integration method.

        Evidence objects are de-duplicated by evidence_id before relation
        detection so that resubmitting the same evidence does not create
        false conflicts (an item is never in conflict with a copy of
        itself). The original objects are never modified.
        """
        result = EvidenceIntegrationResult(evidence=list(evidence_list))
        unique_map: dict[str, Evidence] = {}
        for ev in evidence_list:
            unique_map.setdefault(ev.evidence_id, ev)
        unique_list = list(unique_map.values())
        if len(unique_list) <= 1:
            return result
        result.duplicate_groups = EvidenceIntegrator._detect_duplicates(unique_list)
        conflicts, conflict_refs_by_id = EvidenceIntegrator._detect_conflicts(unique_list)
        result.conflicts = conflicts
        result.conflict_refs_by_id = conflict_refs_by_id
        result.supporting_groups = EvidenceIntegrator._detect_supporting(unique_list)
        return result

    @staticmethod
    def _detect_duplicates(evidence_list: list[Evidence]) -> list[DuplicateGroup]:
        normalized_map: dict[str, list[Evidence]] = {}
        for ev in evidence_list:
            norm = EvidenceIntegrator.normalize_text(ev.content)
            if norm not in normalized_map:
                normalized_map[norm] = []
            normalized_map[norm].append(ev)
        duplicate_groups: list[DuplicateGroup] = []
        for norm_evidences in normalized_map.values():
            if len(norm_evidences) > 1:
                sorted_evidences = sorted(norm_evidences, key=lambda e: e.evidence_id)
                duplicate_groups.append(DuplicateGroup(
                    evidence_ids=[e.evidence_id for e in sorted_evidences],
                    contents=[e.content for e in sorted_evidences],
                ))
        duplicate_groups.sort(key=lambda g: g.evidence_ids[0])
        return duplicate_groups

    @staticmethod
    def _detect_conflicts(evidence_list: list[Evidence]):
        conflicts: list[ConflictRecord] = []
        conflict_refs_by_id: dict[str, frozenset[str]] = {}
        n = len(evidence_list)
        if n <= 1:
            return conflicts, conflict_refs_by_id

        # Pre-compute per-evidence features once to avoid O(n^2) regex work.
        norm_cache: dict[str, str] = {}
        neg_cache: dict[str, list[str]] = {}
        pos_cache: dict[str, list[str]] = {}
        order_cache: dict[str, list[tuple[str, str]]] = {}
        for ev in evidence_list:
            norm = EvidenceIntegrator.normalize_text(ev.content)
            norm_cache[ev.evidence_id] = norm
            neg_cache[ev.evidence_id], pos_cache[ev.evidence_id] = (
                EvidenceIntegrator._classify_statements(norm, _NEGATION_WORDS)
            )
            # 顺序关系必须**按语句**抽取, 所以这里用的不是 norm (它把换行压成
            # 空格), 而是保留换行的归一化文本 —— 理由见 _normalize_statements。
            order_cache[ev.evidence_id] = EvidenceIntegrator._extract_order_relations(
                _normalize_statements(ev.content), _ORDER_PATTERNS
            )

        seen_pairs: set[frozenset[str]] = set()
        for i in range(n):
            ev_a = evidence_list[i]
            norm_a = norm_cache[ev_a.evidence_id]
            neg_a = neg_cache[ev_a.evidence_id]
            pos_a = pos_cache[ev_a.evidence_id]
            order_a = order_cache[ev_a.evidence_id]
            tail_ids = [ev_b.evidence_id for ev_b in evidence_list[i + 1:]]
            neg_b_all = [neg_cache[id_b] for id_b in tail_ids]
            pos_b_all = [pos_cache[id_b] for id_b in tail_ids]
            order_b_all = [order_cache[id_b] for id_b in tail_ids]
            norm_b_all = [norm_cache[id_b] for id_b in tail_ids]
            for off, ev_b in enumerate(evidence_list[i + 1:]):
                pair_key = frozenset((ev_a.evidence_id, ev_b.evidence_id))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                # Fast short-circuit: a conflict requires either an order
                # relation on both sides, or a **否定 vs 肯定** pairing across
                # the two sides (两个来源都否定同一件事是一致, 不是冲突)。
                order_conflict = None
                order_b = order_b_all[off]
                if order_a and order_b:
                    order_conflict = EvidenceIntegrator._order_conflict_from_relations(
                        order_a, order_b
                    )
                if order_conflict:
                    rec = ConflictRecord(
                        evidence_refs=[ev_a.evidence_id, ev_b.evidence_id],
                        description=order_conflict,
                        status=VerificationStatus.PENDING,
                    )
                    rec.conflict_id = EvidenceIntegrator._stable_conflict_id(
                        norm_a, norm_cache[ev_b.evidence_id], order_conflict
                    )
                    conflicts.append(rec)
                    conflict_refs_by_id[rec.conflict_id] = frozenset(
                        (ev_a.evidence_id, ev_b.evidence_id)
                    )
                    continue

                negation_conflict = None
                neg_b = neg_b_all[off]
                pos_b = pos_b_all[off]
                if (neg_a and pos_b) or (neg_b and pos_a):
                    negation_conflict = EvidenceIntegrator._negation_conflict_from_statements(
                        neg_a, pos_a, neg_b, pos_b
                    )
                if negation_conflict:
                    rec = ConflictRecord(
                        evidence_refs=[ev_a.evidence_id, ev_b.evidence_id],
                        description=negation_conflict,
                        status=VerificationStatus.PENDING,
                    )
                    rec.conflict_id = EvidenceIntegrator._stable_conflict_id(
                        norm_a, norm_b_all[off], negation_conflict
                    )
                    conflicts.append(rec)
                    conflict_refs_by_id[rec.conflict_id] = frozenset(
                        (ev_a.evidence_id, ev_b.evidence_id)
                    )

        conflicts.sort(key=lambda c: c.conflict_id)
        return conflicts, conflict_refs_by_id

    @staticmethod
    def _order_conflict_from_relations(
        relations_a: list[tuple[str, str]],
        relations_b: list[tuple[str, str]],
    ) -> Optional[str]:
        for (entity_x, entity_y) in relations_a:
            for (entity_z, entity_w) in relations_b:
                if EvidenceIntegrator._entity_matches(entity_x, entity_w) and EvidenceIntegrator._entity_matches(entity_y, entity_z):
                    return f"Order conflict: one source states '{entity_x} before {entity_y}' while another states '{entity_z} before {entity_w}'"
        return None

    @staticmethod
    def _classify_statements(
        text: str, negation_words: list[str]
    ) -> tuple[list[str], list[str]]:
        """把文本切成语句, 并按"是否含否定词"分成 (否定, 肯定) 两组。

        语句边界是 ``.`` 和 ``;``。判定用 :func:`_negation_pattern` 的
        **整词**匹配 —— 不能用子串, 否则 "fenomeno" 里的 "no" 会让一句
        普通的肯定陈述被当成否定 (见 :func:`_negation_pattern`)。
        """
        negated: list[str] = []
        positive: list[str] = []
        for raw in re.split(r"[.;]", text):
            stmt = raw.strip()
            if not stmt:
                continue
            if any(
                re.search(_negation_pattern(word), stmt, re.IGNORECASE)
                for word in negation_words
            ):
                negated.append(stmt)
            else:
                positive.append(stmt)
        return negated, positive

    @staticmethod
    def _find_negated_statements(text: str, negation_words: list[str]) -> list[str]:
        """返回文本里**含否定词**的语句 (保留旧签名)。"""
        return EvidenceIntegrator._classify_statements(text, negation_words)[0]

    @staticmethod
    def _negation_conflict_from_statements(
        neg_a: list[str],
        pos_a: list[str],
        neg_b: list[str],
        pos_b: list[str],
    ) -> Optional[str]:
        """否定冲突 = 一方的**否定**语句与另一方的**肯定**语句语义相对。

        旧实现比较的是"双方各自的否定语句", 语义上是错的: 两个来源都否定
        同一件事是**一致**, 不是冲突。它当时能"发现"冲突, 靠的是
        ``_negation_pattern`` 的子串匹配缺陷 —— 西班牙语 "fenomeno" 里含
        "no", 于是肯定句也被判成否定句, 两边一比就"冲突"了。把子串匹配
        改成整词匹配之后, 这套错误的比较方式再也发现不了任何冲突
        (``tests/test_knowledge_assembly.py`` 的 8 个冲突用例因此暴露)。
        现在按真正的语义比较: 否定侧 vs 肯定侧, 并由
        :meth:`_is_contradiction` 判定两句是否在说同一件事。
        """
        for neg in neg_a:
            for pos in pos_b:
                if EvidenceIntegrator._is_contradiction(neg, pos):
                    return (
                        f"Negation conflict: one source states '{neg}' "
                        f"while another states '{pos}'"
                    )
        for neg in neg_b:
            for pos in pos_a:
                if EvidenceIntegrator._is_contradiction(neg, pos):
                    return (
                        f"Negation conflict: one source states '{neg}' "
                        f"while another states '{pos}'"
                    )
        return None
    @staticmethod
    def _stable_conflict_id(norm_a: str, norm_b: str, description: str) -> str:
        """Return a deterministic conflict_id so resubmitting equivalent
        evidence does not produce duplicate conflict records.

        The pair (norm_a, norm_b) is ordered so the ID is stable regardless
        of input order.
        """
        a, b = (norm_a, norm_b) if norm_a <= norm_b else (norm_b, norm_a)
        payload = (a + "\n" + b + "\n" + description).encode("utf-8")
        return "conflict-" + hashlib.sha256(payload).hexdigest()[:16]

    @staticmethod
    def _check_conflict(ev_a: Evidence, ev_b: Evidence) -> Optional[ConflictRecord]:
        norm_a = EvidenceIntegrator.normalize_text(ev_a.content)
        norm_b = EvidenceIntegrator.normalize_text(ev_b.content)
        order_conflict = EvidenceIntegrator._check_order_reversal(norm_a, norm_b)
        if order_conflict:
            return ConflictRecord(evidence_refs=[ev_a.evidence_id, ev_b.evidence_id], description=order_conflict, status=VerificationStatus.PENDING)
        negation_conflict = EvidenceIntegrator._check_negation_conflict(norm_a, norm_b)
        if negation_conflict:
            return ConflictRecord(evidence_refs=[ev_a.evidence_id, ev_b.evidence_id], description=negation_conflict, status=VerificationStatus.PENDING)
        return None

    @staticmethod
    def _check_order_reversal(norm_a: str, norm_b: str) -> Optional[str]:
        relations_a = EvidenceIntegrator._extract_order_relations(
            _normalize_statements(norm_a), _ORDER_PATTERNS
        )
        relations_b = EvidenceIntegrator._extract_order_relations(
            _normalize_statements(norm_b), _ORDER_PATTERNS
        )
        if not relations_a or not relations_b:
            return None
        for (entity_x, entity_y) in relations_a:
            for (entity_z, entity_w) in relations_b:
                if EvidenceIntegrator._entity_matches(entity_x, entity_w) and EvidenceIntegrator._entity_matches(entity_y, entity_z):
                    return f"Order conflict: one source states '{entity_x} before {entity_y}' while another states '{entity_z} before {entity_w}'"
        return None

    @staticmethod
    def _extract_order_relations(text: str, patterns: list[str]) -> list[tuple[str, str]]:
        relations: list[tuple[str, str]] = []
        for pattern in patterns:
            matches = re.findall(pattern, text, re.IGNORECASE)
            for match in matches:
                if len(match) == 2:
                    x, y = match[0].strip(), match[1].strip()
                    if x and y:
                        relations.append((x, y))
        return relations

    @staticmethod
    def _entity_matches(entity_a: Optional[str], entity_b: Optional[str]) -> bool:
        if not entity_a or not entity_b:
            return False
        norm_a = EvidenceIntegrator.normalize_text(entity_a)
        norm_b = EvidenceIntegrator.normalize_text(entity_b)
        # Strip punctuation for entity name comparison
        clean_a = re.sub(r'[^\w\s]', '', norm_a).strip()
        clean_b = re.sub(r'[^\w\s]', '', norm_b).strip()
        if not clean_a or not clean_b:
            return False
        return clean_a == clean_b or clean_a in clean_b or clean_b in clean_a

    @staticmethod
    def _check_negation_conflict(norm_a: str, norm_b: str) -> Optional[str]:
        neg_a, pos_a = EvidenceIntegrator._classify_statements(norm_a, _NEGATION_WORDS)
        neg_b, pos_b = EvidenceIntegrator._classify_statements(norm_b, _NEGATION_WORDS)
        return EvidenceIntegrator._negation_conflict_from_statements(
            neg_a, pos_a, neg_b, pos_b
        )

    @staticmethod
    def _find_negated_statements(text: str, negation_words: list[str]) -> list[str]:
        statements = re.split(r"[.;]", text)
        negated: list[str] = []
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            for word in negation_words:
                pattern = _negation_pattern(word)
                if re.search(pattern, stmt, re.IGNORECASE):
                    negated.append(stmt)
                    break
        return negated

    @staticmethod
    def _affirm(statement: str) -> str:
        """把一条否定语句还原成肯定形式 (去掉否定标记)。

        例: ``"la luz no es la causa del fenomeno"`` ->
        ``"la luz es la causa del fenomeno"``。
        """
        out = statement
        for word in _NEGATION_WORDS:
            out = re.sub(_negation_pattern(word), " ", out, flags=re.IGNORECASE)
        return " ".join(out.split())

    @staticmethod
    def _is_contradiction(neg_stmt: str, pos_stmt: str) -> bool:
        """否定语句 (还原成肯定形式后) 与另一条语句是否在说同一件事。

        只数"共享词数"会误判: 西语里 ``es`` / ``en`` / ``la`` 这类功能词
        到处都是, 两条毫不相干的句子很容易凑够 2 个共同词。实际踩到过:
        一条否定陈述与一份中/西/英混排笔记因为共享 ``es``、``en`` 被判成
        冲突 (Task 45 验收时暴露)。所以判定改为两步 —— 先把否定句还原成
        肯定形式, 再要求它与另一条语句**高度重合** (完全相同, 或重合词数
        占较短语句的 60% 以上)。
        """
        affirmed = EvidenceIntegrator._affirm(neg_stmt)
        if not affirmed or not pos_stmt:
            return False
        if affirmed == pos_stmt:
            return True
        words_a = set(affirmed.split())
        words_b = set(pos_stmt.split())
        common = len(words_a & words_b)
        if common < 2:
            return False
        return common / min(len(words_a), len(words_b)) >= 0.6

    @staticmethod
    def _detect_supporting(evidence_list: list[Evidence]) -> list[SupportingGroup]:
        if len(evidence_list) <= 1:
            return []
        normalized_map: dict[str, list[Evidence]] = {}
        for ev in evidence_list:
            norm = EvidenceIntegrator.normalize_text(ev.content)
            if norm not in normalized_map:
                normalized_map[norm] = []
            normalized_map[norm].append(ev)
        supporting_groups: list[SupportingGroup] = []
        seen_groups: set[frozenset[str]] = set()
        # Pre-compute word lists once per normalized text.
        norm_items = list(normalized_map.items())
        word_lists: dict[str, list[str]] = {
            n: n.split() for n in normalized_map
        }
        for i in range(len(norm_items)):
            norm_a, _ = norm_items[i]
            words_a = word_lists[norm_a]
            for j in range(i + 1, len(norm_items)):
                norm_b, _ = norm_items[j]
                words_b = word_lists[norm_b]
                if norm_a == norm_b:
                    continue
                if len(words_a) >= 5 and len(words_b) >= 5:
                    if len(words_a) <= len(words_b):
                        shorter_words, longer_words = words_a, words_b
                    else:
                        shorter_words, longer_words = words_b, words_a
                    for k in range(len(longer_words) - len(shorter_words) + 1):
                        if longer_words[k:k + len(shorter_words)] == shorter_words:
                            group_evidences = normalized_map[norm_a] + normalized_map[norm_b]
                            group_ids = sorted(set(e.evidence_id for e in group_evidences))
                            group_key = frozenset(group_ids)
                            if group_key not in seen_groups:
                                seen_groups.add(group_key)
                                supporting_groups.append(SupportingGroup(evidence_ids=group_ids))
                            break
        supporting_groups.sort(key=lambda g: g.evidence_ids[0])
        return supporting_groups


def integrate(evidence_list: list[Evidence]) -> EvidenceIntegrationResult:
    """Convenience function to integrate evidence."""
    return EvidenceIntegrator.integrate(evidence_list)
