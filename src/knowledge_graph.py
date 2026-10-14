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

文本格式的兼容性 (2026-10-13)
---------------------------
正文解析以前有四处**静默丢失**: 非 AI 路径没有 ``关联:`` 行、写完侧截断到 5 条、
全角冒号切不开、CJK 术语被词字符类整体删空。前两处在写入侧修 (见
``ai/validators.py``), 后两处在本模块修; 另外 ``too_short`` 与自引用现在会带着
原因进 ``skipped_terms``, 而不是凭空消失。

**这个模块只是兼容路径**: 权威关系是 ``knowledge_relations`` 表
(``KnowledgeOrganizationService``), 由 ``Workspace.knowledge_map`` 优先投影。
正文 ``关联:`` 行产出的边在投影里标为 ``origin="content_hint"`` (推断, 无向)。

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
#: **索引与解析必须用同一个谓词** (``_is_matchable``) —— 历史上建索引写
#: ``len(key) > _MIN_TERM_CHARS``、解析写 ``len(key) < _MIN_TERM_CHARS``,
#: 于是恰好 4 个字符的术语 (例如标签 "HTML") 可解析却永不进索引,
#: 只能掉进 token 回退或变成没有任何诊断的 ``too_short``。
_MIN_TERM_CHARS = 4

#: CJK 区块 (汉字 / 扩展 A / 假名)。
#:
#: 为什么必须单列: 原来的词字符类是 ``[0-9a-z\u00e0-\u00ff]``, 中文落在类外,
#: 会被整体替换成空格 —— ``canonical_key("资本")`` 得到空串, 于是**任何中文
#: 术语都不可能匹配上任何知识点**, 而且因为它连 "unresolved" 都不是, 连诊断
#: 都不进, 属于静默消失。项目真实数据里 287/380 条知识点正文含中文标签, 所以
#: 这不是理论问题。
#:
#: 范围只收**能穿过 NFKD 归一**的区块: 汉字 (U+3400-4DBF / U+4E00-9FFF) 与
#: 假名 (U+3040-30FF)。谚文音节在 NFKD 下会分解成 Jamo, 本项目不涉及韩语,
#: 因此不在此声明支持 (宁可不宣称, 也不给一类"看起来支持其实丢字"的输入)。
_CJK_RANGES = "\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff"

#: 非词字符 -> 空格 (CJK 已在词字符类里, 不会被删)。
_TOKEN_SPLIT = re.compile(r"[^0-9a-z\u00e0-\u00ff" + _CJK_RANGES + r"]+")

#: CJK 字符两侧补空格: 中文没有词间分隔, 逐字成 token 是不做词典假设的切法。
#: 精确匹配仍是主路径 (整串一致才命中), 逐字 token 只服务模糊回退。
_CJK_SEPARATE = re.compile(r"([" + _CJK_RANGES + r"])")

#: 关联行分隔符: ASCII 冒号与全角冒号等价 (中文模型/AI 常写全角)。
_RELATION_SEPARATOR = re.compile(r"[:\uff1a]")

#: 关联术语之间的分隔符: 半角与全角斜杠等价。
_RELATION_SLASH = re.compile(r"[/\uff0f]")

#: 匹配时忽略的功能词 (加泰语 / 西语 / 英语混合的最小集合)。
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "from", "into",
    "els", "les", "una", "uns", "unes", "amb", "del", "dels", "dele", "que",
    "per", "dins", "sobre", "com", "mes", "seva", "seu", "seus", "seves",
    "son", "són", "aquest", "aquesta", "aquests", "aquestes",
    "los", "las", "una", "unos", "unas", "con", "por", "para", "sobre",
    "element", "elements", "elemento", "elementos",
})

def _is_cjk(char: str) -> bool:
    """单个字符是否属于 CJK 区块 (汉字 / 假名)。"""
    return "\u3400" <= char <= "\u9fff" or "\u3040" <= char <= "\u30ff"


def canonical_key(text: Any) -> str:
    """归一化键 (只用于匹配, 绝不用于展示)。

    ``NFKD`` 归一 → 小写 → 去组合重音 → CJK 逐字分列 → 非词字符转空格。

    必须是 **NFKD** (兼容分解) 而不是 NFKC: NFKC 会把 ``à`` 保持成单个
    组合字符, 于是 "去掉组合标记" 那一步什么也去不掉, ``Càlcul`` 就匹配
    不上 ``Calcul`` —— 对加泰语/西语课件是致命的 (实测踩过)。

    CJK 逐字分列使中文术语至少是可匹配的 (见 ``_CJK_RANGES``); 拉丁词仍按
    整词保留, 不同术语不会因归一化被并成同一个键 (键保序, 不排序)。
    """
    raw = unicodedata.normalize("NFKD", str(text or "").lower())
    stripped = "".join(ch for ch in raw if not unicodedata.combining(ch))
    spaced = _CJK_SEPARATE.sub(r" \1 ", stripped)
    return " ".join(_TOKEN_SPLIT.sub(" ", spaced).split())


def _is_matchable(key: str) -> bool:
    """索引与解析共用的唯一判据 (见 ``_MIN_TERM_CHARS`` 注释)。"""
    return len(key) >= _MIN_TERM_CHARS


def _content_tokens(text: Any) -> frozenset[str]:
    """匹配用的 token 集 (忽略功能词与过短的拉丁词)。

    CJK 单字**保留**: 中文逐字成 token 后若再按 ``len > 2`` 过滤, 中文
    token 会被全部丢掉, 模糊回退对中文彻底失效 (键相等才有边)。
    """
    out: set[str] = set()
    for token in canonical_key(text).split():
        if token in _STOPWORDS:
            continue
        if len(token) > 2 or (len(token) == 1 and _is_cjk(token)):
            out.add(token)
    return frozenset(out)


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _concept_id(key: str) -> str:
    """概念节点的内容寻址 ID (确定性, 无随机)。"""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return "concept-" + digest


def _relation_terms(content: Any) -> list[str]:
    """取出正文里 ``关联: a / b / c`` 行的术语 (按出现顺序, 去重)。

    兼容性要点 (都是实测过的静默丢失点):

    - **全角冒号**与 ASCII 冒号等价 (``关联：术语`` 以前切不开, 术语会连带
      前缀一起进解析, 必然匹配失败且不留诊断)。
    - 半角/全角斜杠都当分隔符。
    - 没有冒号时, 整行去掉前缀即内容 (旧写法 ``split(":")[-1]`` 在无冒号时
      会把前缀一起当术语)。
    """
    out: list[str] = []
    seen: set[str] = set()
    for line in str(content or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith(CONTENT_RELATION_PREFIX):
            continue
        body = stripped[len(CONTENT_RELATION_PREFIX):]
        if _RELATION_SEPARATOR.search(body):
            body = _RELATION_SEPARATOR.split(body, 1)[1]
        for raw in _RELATION_SLASH.split(body):
            term = raw.strip()
            if term and term not in seen:
                seen.add(term)
                out.append(term)
    return out


#: 边来源标识 (投影里每一条边都带 ``origin``, 不许混为一谈)。
EDGE_ORIGIN_REGISTERED = "registered"      #: ``knowledge_relations`` 里的一等事实 (有向)
EDGE_ORIGIN_CONTENT_HINT = "content_hint"  #: 正文 ``关联:`` 行归一化后的推断 (无向)
EDGE_ORIGIN_CONCEPT = "concept"            #: 未归类概念节点的虚线连接 (非知识点)


def build_knowledge_graph(
    knowledge_points: Sequence[Mapping[str, Any]],
    materials: Sequence[Mapping[str, Any]],
    material_to_knowledge: Optional[Mapping[str, Iterable[str]]] = None,
    *,
    token_threshold: float = DEFAULT_TOKEN_THRESHOLD,
    min_concept_refs: int = DEFAULT_MIN_CONCEPT_REFS,
    relations: Optional[Sequence[Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """把知识点投影成图 (节点 / 边 / 概念 / 未匹配术语 / 统计)。

    返回结构 (全部有序, 可直接进 HTTP 响应)::

        {
          "nodes":   [{id, kind, label, material_id, degree, generation, status}],
          "edges":   [{"source", "target", "kind", "origin", "directed", "relation_type"}],
                     # kind: kp | concept; origin: registered | content_hint | concept
          "concepts":[{id, label, refs: [kp_id, ...]}],
          "unresolved_terms": [{"term", "count"}],
          "skipped_terms":[{"term", "count", "reason"}],
          "stats":   {node_count, edge_count, concept_count, skipped_term_count, ...},
        }

    ``unresolved_terms`` = 被引用但**匹配不到任何知识点**的术语 (可能物化成概念);
    ``skipped_terms`` = 按设计不参与匹配的术语 (``too_short`` / ``self_reference``),
    带原因, 仅供诊断 —— 它们绝不物化成概念节点。
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

    # ---- 已登记关系 (权威、有向、有类型) ----
    #
    # 这是本投影的**首选**边的来源: ``knowledge_relations`` 里的一等事实。
    # 正文 ``关联:`` 行只在下面做**兼容回退**, 并且不给已经登记过的对子再画
    # 一条重复边。端点不在本图 (跨课引用 / 已注销) 或自引用 -> 不画, 但计入
    # ``dropped_relation_count``, 不静默。
    kp_id_set = set(kp_ids)
    registered_pairs: set[tuple[str, str]] = set()
    dropped_relations = 0
    registered_untyped: list[dict[str, Any]] = []
    for relation in relations or ():
        source = str(relation.get("source_knowledge_point_id") or "")
        target = str(relation.get("target_knowledge_point_id") or "")
        if not source or not target or source == target:
            dropped_relations += 1
            continue
        if source not in kp_id_set or target not in kp_id_set:
            dropped_relations += 1
            continue
        registered_pairs.add((source, target) if source <= target else (target, source))
        registered_untyped.append({
            "source": source,
            "target": target,
            "kind": "kp",
            "origin": EDGE_ORIGIN_REGISTERED,
            "directed": True,
            "relation_type": str(relation.get("relation_type") or ""),
        })
    registered_edges = sorted(
        registered_untyped,
        key=lambda edge: (edge["source"], edge["target"], edge["relation_type"]),
    )

    # ---- 归一化索引 (匹配用) ----
    title_index: dict[str, str] = {}
    term_index: dict[str, str] = {}
    for kid in kp_ids:
        key = canonical_key(labels[kid])
        if _is_matchable(key):
            title_index.setdefault(key, kid)
        for term in terms_by_kp[kid]:
            key = canonical_key(term)
            if _is_matchable(key):
                term_index.setdefault(key, kid)
    token_index: list[tuple[frozenset[str], str]] = [
        (_content_tokens(labels[kid]), kid) for kid in kp_ids
    ]

    def resolve(term: str) -> tuple[Optional[str], str]:
        key = canonical_key(term)
        if not _is_matchable(key):
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
    #: 被跳过但**有原因**的术语 (``too_short`` / ``self_reference``)。
    #: 以前这些情况直接消失, 既不成边也不进任何报告 —— 排查时无从下手。
    skipped_counts: dict[tuple[str, str], int] = {}
    relation_instances = 0
    for kid in kp_ids:
        for term in _relation_terms(contents[kid]):
            relation_instances += 1
            target, how = resolve(term)
            if target is not None and target != kid:
                pair = tuple(sorted((kid, target)))
                # 已有已登记关系时不重复报同一条关联 (同一句话两个来源)。
                if pair not in registered_pairs:
                    edges.add(pair)
            elif target == kid:
                # 指回自己: 不算边, 但如实记录原因。
                key = (term, "self_reference")
                skipped_counts[key] = skipped_counts.get(key, 0) + 1
            elif how == "unresolved":
                unresolved_count[term] = unresolved_count.get(term, 0) + 1
                unresolved_refs.setdefault(term, set()).add(kid)
            else:
                # ``too_short``: 术语过短, 按设计不参与匹配 —— 但仍要可见。
                key = (term, how or "unresolved")
                skipped_counts[key] = skipped_counts.get(key, 0) + 1

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
    for edge in registered_edges:
        degree[edge["source"]] = degree.get(edge["source"], 0) + 1
        degree[edge["target"]] = degree.get(edge["target"], 0) + 1
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

    hint_edges: list[dict[str, Any]] = []
    for left, right in sorted(edges):
        is_concept = left.startswith("concept-") or right.startswith("concept-")
        hint_edges.append({
            "source": left,
            "target": right,
            "kind": "concept" if is_concept else "kp",
            "origin": EDGE_ORIGIN_CONCEPT if is_concept else EDGE_ORIGIN_CONTENT_HINT,
            "directed": False,
            "relation_type": "",
        })
    #: 已登记边在前 (权威), 文本推断边在后; 各自内部已排序 -> 整体确定。
    edge_list = registered_edges + hint_edges

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
        #: 被跳过但可诊断的术语 (原因 + 次数)。**不是**知识点, 也绝不物化成概念。
        "skipped_terms": [
            {"term": term, "count": count, "reason": reason}
            for (term, reason), count in sorted(
                skipped_counts.items(),
                key=lambda kv: (-kv[1], canonical_key(kv[0][0]), kv[0][1]),
            )
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
            "registered_relation_count": len(registered_edges),
            "content_hint_edge_count": sum(
                1 for edge in edge_list if edge["origin"] == EDGE_ORIGIN_CONTENT_HINT
            ),
            "dropped_relation_count": dropped_relations,
            "unresolved_term_count": len(unresolved_count),
            "skipped_term_count": len(skipped_counts),
        },
    }
