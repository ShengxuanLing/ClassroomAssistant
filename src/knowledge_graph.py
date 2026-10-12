# -*- coding: utf-8 -*-
"""知识图谱投影 (只读, 确定性)。

把已经存在的数据连成图::

    知识点 (KnowledgePoint) ──关联──▶ 知识点
    知识点 ──引用──▶ 未归类概念 (原文明示的术语, 不是知识点)

数据来源 (全部来自既有事实, 不新增任何写入):

- 边的候选来自知识点正文里的 ``关联: …`` 行 —— 这是 AI 理解管线
  (``ai/validators.py::_candidate_knowledge_content``) 把候选的
  ``relations`` 拼进正文的结果, 也就是说**关系数据一直存在**, 只是从来没
  有被归一化成图上的边。
- 归一化 (EDC 框架里的 Canonicalize 步骤) 把自由文本术语解析到已有知识点:
  ``精确归一化命中标题 → 精确命中 original_terms → token Jaccard ≥ 阈值``。
  三级都命中不了就**不造边**, 如实记进 ``unresolved_terms``。

为什么不把未匹配术语丢掉
------------------------
被 **≥2 个**知识点共同引用、却匹配不到任何知识点的术语, 说明课程里存在一个
**还没被登记成知识点**的概念。把它物化成 ``kind="concept"`` 的节点连上引用者,
既让图更完整, 又**不伪造知识点** —— 概念节点与知识点在数据形状 (独立的
``concepts`` 列表 + ``kind`` 字段) 和 UI 呈现 (虚线) 上都可区分。

硬性原则 (与项目一致)
--------------------
- **绝不编造**: 没有 ``关联:`` 行就没有边; 解析不到就不连。
- **确定性**: 输出全部排序; 概念 ID 内容寻址 (sha256); 无 random / uuid4 /
  ``hash()`` / 时间。
- **只读**: 纯函数, 不写库、不改输入 (输入是 API 投影用的 dict, 不做原地修改)。
- **不改写原文**: 归一化只用于匹配; 展示用原文术语。
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Iterable, Mapping, Optional, Sequence

__all__ = [
    "build_knowledge_graph",
    "canonical_key",
    "CONTENT_RELATION_PREFIX",
    "DEFAULT_TOKEN_THRESHOLD",
    "DEFAULT_MIN_CONCEPT_REFS",
]

#: 知识点正文里关联行的前缀 (由 ai/validators.py 写入; 见模块 docstring)。
CONTENT_RELATION_PREFIX = "关联"

#: token 重合度阈值 —— 低于它不算"同一个概念"。
DEFAULT_TOKEN_THRESHOLD = 0.6

#: 被多少个知识点引用才把未匹配术语物化成概念节点。
DEFAULT_MIN_CONCEPT_REFS = 2

#: 太短的术语不参与匹配 (避免 "CSS" 之类命中一切)。
_MIN_TERM_CHARS = 4

#: 匹配时忽略的功能词 (加泰语 / 西语 / 英语混合的最小集合)。
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into",
    "els", "les", "una", "uns", "unes", "amb", "del", "dels", "dele", "que",
    "per", "dins", "sobre", "com", "mes", "seva", "seu", "seus", "seves",
    "son", "són", "aquest", "aquesta", "aquests", "aquestes",
    "los", "las", "una", "unos", "unas", "con", "por", "para", "sobre",
    "element", "elements", "elemento", "elementos",
})

_TOKEN_SPLIT = re.compile(r"[^0-9a-z\u00e0-\u00ff]+")


def canonical_key(text: Any) -> str:
    """归一化键 (只用于匹配, 绝不用于展示)。

    ``NFKD`` 归一 → 小写 → 去组合重音 → 非字母数字转空格 → 压缩空白。

    必须是 **NFKD** (兼容分解) 而不是 NFKC: NFKC 会把 ``à`` 保持成单个
    组合字符, 于是 "去掉组合标记" 那一步什么也去不掉, ``Càlcul`` 就匹配
    不上 ``Calcul`` —— 对加泰语/西语课件是致命的 (实测踩过)。
    """
    raw = unicodedata.normalize("NFKD", str(text or "").lower())
    stripped = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return " ".join(_TOKEN_SPLIT.sub(" ", stripped).split())


def _content_tokens(text: Any) -> frozenset[str]:
    return frozenset(
        token for token in canonical_key(text).split()
        if len(token) > 2 and token not in _STOPWORDS
    )


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _concept_id(key: str) -> str:
    """概念节点的内容寻址 ID (确定性, 无随机)。"""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return "concept-" + digest


def _relation_terms(content: Any) -> list[str]:
    """取出正文里 ``关联: a / b / c`` 行的术语 (按出现顺序, 去重)。"""
    out: list[str] = []
    seen: set[str] = set()
    for line in str(content or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith(CONTENT_RELATION_PREFIX):
            continue
        for raw in stripped.split(":", 1)[-1].split("/"):
            term = raw.strip()
            if term and term not in seen:
                seen.add(term)
                out.append(term)
    return out


def build_knowledge_graph(
    knowledge_points: Sequence[Mapping[str, Any]],
    materials: Sequence[Mapping[str, Any]],
    material_to_knowledge: Optional[Mapping[str, Iterable[str]]] = None,
    *,
    token_threshold: float = DEFAULT_TOKEN_THRESHOLD,
    min_concept_refs: int = DEFAULT_MIN_CONCEPT_REFS,
) -> dict[str, Any]:
    """把知识点投影成图 (节点 / 边 / 概念 / 未匹配术语 / 统计)。

    返回结构 (全部有序, 可直接进 HTTP 响应)::

        {
          "nodes":   [{id, kind, label, material_id, degree, generation, status}],
          "edges":   [{"source", "target", "kind"}],   # kind: kp | concept
          "concepts":[{id, label, refs: [kp_id, ...]}],
          "unresolved_terms": [{"term", "count"}],
          "stats":   {node_count, edge_count, concept_count, ...},
        }
    """
    kp_ids: list[str] = []
    labels: dict[str, str] = {}
    contents: dict[str, str] = {}
    terms_by_kp: dict[str, list[str]] = {}
    meta: dict[str, dict[str, Any]] = {}
    for point in knowledge_points or ():
        kid = str(point.get("knowledge_id") or "")
        if not kid:
            continue
        kp_ids.append(kid)
        labels[kid] = str(point.get("title") or kid)
        contents[kid] = str(point.get("content") or "")
        terms_by_kp[kid] = [str(t) for t in (point.get("original_terms") or [])]
        meta[kid] = {
            "generation": str(point.get("generation_mode") or ""),
            "status": str(point.get("validation_status") or ""),
        }

    # 知识点 -> 材料 (反向索引由调用方给出; 缺省时按"无材料"处理)。
    kp_material: dict[str, str] = {}
    for material_id, ids in (material_to_knowledge or {}).items():
        for kid in ids or ():
            kp_material.setdefault(str(kid), str(material_id))

    # ---- 归一化索引 (匹配用) ----
    title_index: dict[str, str] = {}
    term_index: dict[str, str] = {}
    for kid in kp_ids:
        key = canonical_key(labels[kid])
        if key:
            title_index.setdefault(key, kid)
        for term in terms_by_kp[kid]:
            key = canonical_key(term)
            if len(key) > _MIN_TERM_CHARS:
                term_index.setdefault(key, kid)
    token_index: list[tuple[frozenset[str], str]] = [
        (_content_tokens(labels[kid]), kid) for kid in kp_ids
    ]

    def resolve(term: str) -> tuple[Optional[str], str]:
        key = canonical_key(term)
        if len(key) < _MIN_TERM_CHARS:
            return None, "too_short"
        if key in title_index:
            return title_index[key], "exact_title"
        if key in term_index:
            return term_index[key], "exact_term"
        tokens = _content_tokens(term)
        best: Optional[str] = None
        best_score = 0.0
        for candidate_tokens, kid in token_index:
            score = _jaccard(tokens, candidate_tokens)
            if score > best_score:
                best, best_score = kid, score
        if best is not None and best_score >= token_threshold:
            return best, "token_overlap"
        return None, "unresolved"

    # ---- 连边 ----
    edges: set[tuple[str, str]] = set()
    unresolved_count: dict[str, int] = {}
    unresolved_refs: dict[str, set[str]] = {}
    relation_instances = 0
    for kid in kp_ids:
        for term in _relation_terms(contents[kid]):
            relation_instances += 1
            target, how = resolve(term)
            if target is not None and target != kid:
                edges.add(tuple(sorted((kid, target))))
            elif how == "unresolved":
                unresolved_count[term] = unresolved_count.get(term, 0) + 1
                unresolved_refs.setdefault(term, set()).add(kid)

    # ---- 未匹配但被多个知识点引用的术语 -> 概念节点 ----
    concepts: list[dict[str, Any]] = []
    for term in sorted(unresolved_refs, key=lambda t: (-len(unresolved_refs[t]), canonical_key(t))):
        refs = sorted(unresolved_refs[term])
        if len(refs) < max(1, int(min_concept_refs)):
            continue
        concept_id = _concept_id(canonical_key(term))
        concepts.append({"id": concept_id, "label": term, "refs": refs})
        for kid in refs:
            edges.add(tuple(sorted((concept_id, kid))))

    # ---- 节点 ----
    degree: dict[str, int] = {}
    for left, right in edges:
        degree[left] = degree.get(left, 0) + 1
        degree[right] = degree.get(right, 0) + 1

    nodes: list[dict[str, Any]] = []
    for kid in sorted(kp_ids):
        nodes.append({
            "id": kid,
            "kind": "kp",
            "label": labels[kid],
            "material_id": kp_material.get(kid, ""),
            "degree": degree.get(kid, 0),
            "generation": meta[kid]["generation"],
            "status": meta[kid]["status"],
        })
    for concept in concepts:
        nodes.append({
            "id": concept["id"],
            "kind": "concept",
            "label": concept["label"],
            "material_id": "",
            "degree": degree.get(concept["id"], 0),
            "generation": "concept",
            "status": "unclassified",
        })

    edge_list = [
        {"source": left, "target": right,
         "kind": "concept" if (left.startswith("concept-") or right.startswith("concept-")) else "kp"}
        for left, right in sorted(edges)
    ]

    kp_nodes = [n for n in nodes if n["kind"] == "kp"]
    linked = sum(1 for n in kp_nodes if n["degree"] > 0)
    return {
        "nodes": nodes,
        "edges": edge_list,
        "concepts": concepts,
        "unresolved_terms": [
            {"term": term, "count": count}
            for term, count in sorted(unresolved_count.items(), key=lambda kv: (-kv[1], canonical_key(kv[0])))
        ],
        "stats": {
            "knowledge_point_count": len(kp_nodes),
            "material_count": len(materials or ()),
            "edge_count": len(edge_list),
            "concept_edge_count": sum(1 for e in edge_list if e["kind"] == "concept"),
            "concept_count": len(concepts),
            "linked_knowledge_point_count": linked,
            "isolated_knowledge_point_count": len(kp_nodes) - linked,
            "relation_instance_count": relation_instances,
            "unresolved_term_count": len(unresolved_count),
        },
    }
