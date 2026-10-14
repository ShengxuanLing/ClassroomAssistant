# -*- coding: utf-8 -*-
"""知识图谱投影 (``src/knowledge_graph.py``) 回归测试。

背景
----
知识地图此前**没有关系层**: ``knowledge-map`` 返回的 ``relations`` 恒为空,
于是"地图"只能列标题 (用户 2026-10-12 实测反馈「你管这玩意叫地图么」)。
但关系数据其实一直存在于知识点正文的 ``关联: …`` 行里 —— 缺的是把自由文本
术语归一化 (EDC 的 Canonicalize 步骤) 成图上那一步。

本文件锁住该投影的语义:

- 三条解析回退链 (精确标题 → 精确 original_terms → token Jaccard ≥ 阈值);
- **解析不到就不造边** (绝不编造);
- 被 ≥2 个知识点引用且解析不到的术语物化为 ``concept`` 节点, 且
  **概念节点绝不进知识点计数** (不冒充 KnowledgePoint);
- 输出确定性 (同一输入两次结果逐字节相同)、无悬空边、无自环。

每条断言都是变异杀手: 回退任一条规则都会红。
"""

from __future__ import annotations

import json

from src.knowledge_graph import (
    DEFAULT_MIN_CONCEPT_REFS,
    build_knowledge_graph,
    canonical_key,
)


def kp(kid: str, title: str, relations=(), terms=(), content=None):
    """构造一个 API 形状的知识点 dict (与 knowledge_map 投影一致)。"""
    if content is None:
        body = "Una descripció qualsevol."
        if relations:
            body += "\n关联: " + " / ".join(relations)
        content = body
    return {
        "knowledge_id": kid,
        "title": title,
        "content": content,
        "original_terms": list(terms),
        "generation_mode": "ai_summary",
        "validation_status": "supported",
    }


def graph(points, materials=None, mapping=None, **kwargs):
    return build_knowledge_graph(
        points,
        materials if materials is not None else [{"material_id": "mat-1", "filename": "a.pdf"}],
        mapping if mapping is not None else {"mat-1": [p["knowledge_id"] for p in points]},
        **kwargs,
    )


def edge_pairs(result):
    return {(e["source"], e["target"]) for e in result["edges"]}


# ---------------------------------------------------------------------------
# 1. 空输入
# ---------------------------------------------------------------------------

def test_empty_input_yields_empty_graph():
    result = graph([])
    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["concepts"] == []
    assert result["unresolved_terms"] == []
    assert result["stats"]["knowledge_point_count"] == 0
    assert result["stats"]["edge_count"] == 0


# ---------------------------------------------------------------------------
# 2. 三条解析回退链
# ---------------------------------------------------------------------------

def test_exact_title_match_creates_edge():
    """关联术语与另一知识点的标题**逐字**相同 -> 连边。"""
    a = kp("kp-a", "Model de caixa", relations=["Selector CSS"])
    b = kp("kp-b", "Selector CSS")
    result = graph([a, b])
    assert edge_pairs(result) == {("kp-a", "kp-b")}
    assert result["stats"]["edge_count"] == 1


def test_exact_title_match_is_accent_insensitive():
    """归一化去重音: ``Càlcul`` 与 ``calcul`` 视为同一概念。"""
    a = kp("kp-a", "Derivades", relations=["Càlcul diferencial"])
    b = kp("kp-b", "Calcul diferencial")
    result = graph([a, b])
    assert ("kp-a", "kp-b") in edge_pairs(result)


def test_original_terms_match_creates_edge():
    """术语命中另一知识点的 ``original_terms`` 也算 (标题不同也能连)。"""
    a = kp("kp-a", "Estructura del document", relations=["etiqueta arrel"])
    b = kp("kp-b", "Element html", terms=["etiqueta arrel"])
    result = graph([a, b])
    assert ("kp-a", "kp-b") in edge_pairs(result)


def test_token_overlap_match_creates_edge():
    """标题有措辞差异但 token 重合度 >= 阈值时仍连边。"""
    a = kp("kp-a", "Comentaris al codi", relations=["sintaxi de comentari html"])
    b = kp("kp-b", "Sintaxi de comentari HTML al codi")
    result = graph([a, b])
    assert ("kp-a", "kp-b") in edge_pairs(result)


def test_token_overlap_below_threshold_does_not_create_edge():
    """重合度不够 -> 不连边, 也不物化 (只被 1 个点引用)。"""
    a = kp("kp-a", "Comentaris al codi", relations=["tipus de dades numeriques"])
    b = kp("kp-b", "Sintaxi de comentari HTML")
    result = graph([a, b])
    assert edge_pairs(result) == set()
    assert result["stats"]["edge_count"] == 0
    assert any(u["term"] == "tipus de dades numeriques" for u in result["unresolved_terms"])


# ---------------------------------------------------------------------------
# 3. 未匹配术语: 不编造边, 但被多点引用时物化成概念节点
# ---------------------------------------------------------------------------

def test_short_term_is_never_matched():
    """过短术语 (如 "CSS") 不参与匹配 —— 否则会命中一切。"""
    a = kp("kp-a", "Selectors", relations=["CSS"])
    b = kp("kp-b", "CSS")
    result = graph([a, b])
    assert edge_pairs(result) == set()


def test_unresolved_term_referenced_once_is_listed_not_drawn():
    a = kp("kp-a", "Un punt", relations=["terme que no existeix enlloc"])
    result = graph([a])
    assert result["edges"] == []
    assert result["concepts"] == []
    assert [u["term"] for u in result["unresolved_terms"]] == ["terme que no existeix enlloc"]
    assert result["unresolved_terms"][0]["count"] == 1


def test_unresolved_term_referenced_twice_becomes_concept_node():
    """被 >=2 个知识点引用却匹配不到 -> 物化成 concept 节点并连上引用者。"""
    term = "componentes de la entrega final"
    a = kp("kp-a", "Primer punt", relations=[term])
    b = kp("kp-b", "Segon punt", relations=[term])
    result = graph([a, b])

    assert len(result["concepts"]) == 1
    concept = result["concepts"][0]
    assert concept["label"] == term
    assert concept["refs"] == ["kp-a", "kp-b"]
    assert edge_pairs(result) == {
        tuple(sorted((concept["id"], "kp-a"))),
        tuple(sorted((concept["id"], "kp-b"))),
    }
    assert result["stats"]["concept_edge_count"] == 2


def test_min_concept_refs_is_configurable():
    term = "concepte orfe"
    a = kp("kp-a", "Primer punt", relations=[term])
    result = graph([a], min_concept_refs=1)
    assert len(result["concepts"]) == 1
    assert DEFAULT_MIN_CONCEPT_REFS == 2  # 默认仍是 2


def test_concept_node_never_counts_as_knowledge_point():
    """**红线**: 概念节点绝不进知识点计数 (不冒充 KnowledgePoint)。"""
    term = "concepte compartit"
    a = kp("kp-a", "Primer punt", relations=[term])
    b = kp("kp-b", "Segon punt", relations=[term])
    result = graph([a, b])

    assert result["stats"]["knowledge_point_count"] == 2  # 不是 3
    assert result["stats"]["concept_count"] == 1
    kp_ids = {n["id"] for n in result["nodes"] if n["kind"] == "kp"}
    assert kp_ids == {"kp-a", "kp-b"}
    concept_ids = {n["id"] for n in result["nodes"] if n["kind"] == "concept"}
    assert len(concept_ids) == 1
    assert not (kp_ids & concept_ids)
    for concept_id in concept_ids:
        assert concept_id.startswith("concept-")


def test_concept_ids_are_content_addressed_and_stable():
    term = "concepte estable"
    a = kp("kp-a", "Primer punt", relations=[term])
    b = kp("kp-b", "Segon punt", relations=[term])
    first = graph([a, b])["concepts"][0]["id"]
    second = graph([a, b])["concepts"][0]["id"]
    assert first == second
    assert first == graph([b, a])["concepts"][0]["id"]  # 输入顺序无关


# ---------------------------------------------------------------------------
# 4. 结构完整性
# ---------------------------------------------------------------------------

def test_no_self_edges_and_no_dangling_edges():
    a = kp("kp-a", "Selectors CSS", relations=["Selectors CSS", "Model de caixa"])
    b = kp("kp-b", "Model de caixa", relations=["Selectors CSS"])
    result = graph([a, b])

    node_ids = {n["id"] for n in result["nodes"]}
    for edge in result["edges"]:
        assert edge["source"] != edge["target"], "自环不该出现"
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids


def test_stats_are_internally_consistent():
    a = kp("kp-a", "Selectors CSS", relations=["Model de caixa", "terme orfe compartit"])
    b = kp("kp-b", "Model de caixa", relations=["terme orfe compartit"])
    c = kp("kp-c", "Punt solitari")
    result = graph([a, b, c])

    stats = result["stats"]
    assert stats["edge_count"] == len(result["edges"])
    assert stats["concept_count"] == len(result["concepts"])
    assert (stats["linked_knowledge_point_count"]
            + stats["isolated_knowledge_point_count"]) == stats["knowledge_point_count"]
    assert stats["isolated_knowledge_point_count"] == 1  # kp-c
    assert stats["material_count"] == 1


def test_edges_are_undirected_and_deduplicated():
    a = kp("kp-a", "Selectors CSS", relations=["Model de caixa"])
    b = kp("kp-b", "Model de caixa", relations=["Selectors CSS"])
    result = graph([a, b])
    assert len(result["edges"]) == 1
    assert edge_pairs(result) == {tuple(sorted(("kp-a", "kp-b")))}


def test_output_is_deterministic_byte_for_byte():
    points = [
        kp("kp-a", "Selectors CSS", relations=["Model de caixa", "terme orfe compartit"]),
        kp("kp-b", "Model de caixa", relations=["terme orfe compartit"]),
        kp("kp-c", "Punt solitari"),
    ]
    first = json.dumps(graph(points), ensure_ascii=False, sort_keys=True)
    second = json.dumps(graph(points), ensure_ascii=False, sort_keys=True)
    assert first == second


def test_original_text_is_preserved_verbatim():
    """归一化只用于匹配; 展示标签逐字保留原文 (含重音与大小写)。"""
    a = kp("kp-a", "Derivades", relations=["Càlcul Diferencial"])
    b = kp("kp-b", "Calcul diferencial")
    result = graph([a, b])
    labels = {n["id"]: n["label"] for n in result["nodes"]}
    assert labels["kp-a"] == "Derivades"
    assert labels["kp-b"] == "Calcul diferencial"   # 原样, 不归一化


def test_canonical_key_normalizes_but_keeps_words():
    assert canonical_key("  Càlcul   Diferencial ") == "calcul diferencial"
    assert canonical_key("HTML/CSS") == "html css"
    assert canonical_key(None) == ""


def test_missing_relation_lines_produce_no_edges():
    """没有 ``关联:`` 行 -> 没有边 (绝不凭相似标题造边)。"""
    a = kp("kp-a", "Selectors CSS", content="Només text, cap línia d'associació.")
    b = kp("kp-b", "Model de caixa CSS", content="També sense associacions.")
    result = graph([a, b])
    assert result["edges"] == []
    assert result["stats"]["edge_count"] == 0


# ---------------------------------------------------------------------------
# 5. 文本兼容路径: 中文 / 全角标点 / 短术语诊断 / 无静默截断
#     (2026-10-13 修复; 每一条都对应一个曾经静默丢失的形状)
# ---------------------------------------------------------------------------

def test_cjk_term_matches_cjk_title():
    """中文术语以前被词字符类整体删空 (canonical_key -> "") -> 永远无边。"""
    a = kp("kp-a", "Concepte base", relations=["函数定义"])
    b = kp("kp-b", "函数定义")
    result = graph([a, b])
    assert edge_pairs(result) == {("kp-a", "kp-b")}


def test_cjk_term_matches_cjk_original_terms():
    a = kp("kp-a", "甲", relations=["标签根元素"])
    b = kp("kp-b", "乙", terms=["标签根元素"])
    assert ("kp-a", "kp-b") in edge_pairs(graph([a, b]))


def test_cjk_fuzzy_overlap_creates_edge():
    """汉字逐字成 token 后, 前缀关系 (集合论 vs 集合论基础) 可连。"""
    a = kp("kp-a", "集合论", relations=["集合论基础"])
    b = kp("kp-b", "集合论基础")
    assert ("kp-a", "kp-b") in edge_pairs(graph([a, b]))


def test_cjk_short_term_is_reported_and_never_matched():
    """两字术语过短 -> 不连边, 但必须出现在诊断里 (以前连诊断都没有)。"""
    a = kp("kp-a", "甲", relations=["函数"])
    result = graph([a])
    assert result["edges"] == []
    assert result["concepts"] == []
    assert result["skipped_terms"] == [
        {"term": "函数", "count": 1, "reason": "too_short"}
    ]
    assert result["unresolved_terms"] == []  # 过短不是 unresolved, 不许混进去
    assert result["stats"]["skipped_term_count"] == 1


def test_full_width_colon_and_slash_are_equivalent_to_ascii():
    """中文模型爱写全角冒号/斜杠; 旧分割只认 ASCII ``:``/``/``。"""
    # 同一个 kp-a, 只换标点写法 —— 结果必须逐项相同。
    full = kp("kp-a", "Nucli", content="text\n关联：Model de caixa／Selectors CSS")
    ascii_ = kp("kp-a", "Nucli", content="text\n关联: Model de caixa / Selectors CSS")
    b = kp("kp-b", "Model de caixa")
    c = kp("kp-c", "Selectors CSS")
    assert edge_pairs(graph([full, b, c])) == edge_pairs(graph([ascii_, b, c]))
    assert edge_pairs(graph([full, b, c])) == {("kp-a", "kp-b"), ("kp-a", "kp-c")}


def test_four_character_term_is_indexed_and_matched():
    """索引用 ``>`` 而解析用 ``<`` 的 off-by-one: 恰好 4 字符的术语永不命中。"""
    a = kp("kp-a", "Document", relations=["HTML"])
    b = kp("kp-b", "HTML")
    assert ("kp-a", "kp-b") in edge_pairs(graph([a, b]))


def test_three_character_term_still_refused():
    """红线: 修 4 字符不等于放开短缩写 —— "CSS" 仍必须拒绝。"""
    a = kp("kp-a", "Selectors", relations=["CSS"])
    b = kp("kp-b", "CSS")
    result = graph([a, b])
    assert result["edges"] == []
    assert result["stats"]["skipped_term_count"] >= 1


def test_self_reference_is_reported_not_silently_dropped():
    a = kp("kp-a", "函数定义", relations=["函数定义"])
    result = graph([a])
    assert result["edges"] == []  # 自环仍不画
    assert result["skipped_terms"] == [
        {"term": "函数定义", "count": 1, "reason": "self_reference"}
    ]


def test_many_relations_are_not_truncated():
    """8 条关联 = 8 次关联实例、8 条边 (旧写入侧 ``[:5]`` 会丢 3 条)。"""
    other = ["kp-%d" % i for i in range(1, 9)]
    titles = ["Ter" + "me%d" % i for i in range(1, 9)]
    a = kp("kp-a", "Punt central", relations=titles)
    points = [a] + [kp(other[i], titles[i]) for i in range(8)]
    result = graph(points)
    assert result["stats"]["relation_instance_count"] == 8
    assert result["stats"]["edge_count"] == 8


def test_skipped_terms_never_become_concept_nodes():
    """被跳过 (过短) 的术语即使被多个点引用, 也不能物化成概念节点。"""
    a = kp("kp-a", "甲", relations=["函数"])
    b = kp("kp-b", "乙", relations=["函数"])
    result = graph([a, b])
    assert result["concepts"] == []
    assert result["edges"] == []
    assert result["skipped_terms"] == [
        {"term": "函数", "count": 2, "reason": "too_short"}
    ]


def test_skipped_terms_are_deterministic():
    a = kp("kp-a", "甲", relations=["函数", "CSS"])
    b = kp("kp-b", "乙", relations=["CSS"])
    first = json.dumps(graph([a, b])["skipped_terms"], ensure_ascii=False, sort_keys=True)
    second = json.dumps(graph([a, b])["skipped_terms"], ensure_ascii=False, sort_keys=True)
    assert first == second


# ---------------------------------------------------------------------------
# 6. 权威关系 (已登记) 优先; 文本推断边降级为兼容回退
#     (2026-10-13: 以前地图只有文本边, 权威的 knowledge_relations 无人消费)
# ---------------------------------------------------------------------------

def rel(source, target, relation_type="prerequisite"):
    return {
        "relation_id": "krel-" + source + target,
        "course_id": "c",
        "source_knowledge_point_id": source,
        "target_knowledge_point_id": target,
        "relation_type": relation_type,
    }


def test_registered_relation_is_directed_and_typed():
    a = kp("kp-a", "Alfa", content="sense associacions")
    b = kp("kp-b", "Beta", content="sense associacions")
    result = graph([a, b], relations=[rel("kp-a", "kp-b", "prerequisite")])
    assert len(result["edges"]) == 1
    edge = result["edges"][0]
    assert (edge["source"], edge["target"]) == ("kp-a", "kp-b")  # 方向不丢
    assert edge["origin"] == "registered"
    assert edge["directed"] is True
    assert edge["relation_type"] == "prerequisite"
    assert result["stats"]["registered_relation_count"] == 1
    assert result["stats"]["content_hint_edge_count"] == 0
    # 权威边同样进度数与孤立点统计。
    assert result["stats"]["linked_knowledge_point_count"] == 2
    assert result["stats"]["isolated_knowledge_point_count"] == 0


def test_reverse_direction_is_a_distinct_registered_edge():
    a = kp("kp-a", "Alfa", content="x")
    b = kp("kp-b", "Beta", content="x")
    result = graph([a, b], relations=[rel("kp-a", "kp-b"), rel("kp-b", "kp-a")])
    assert [(e["source"], e["target"]) for e in result["edges"]] == [
        ("kp-a", "kp-b"), ("kp-b", "kp-a"),
    ]


def test_registered_pair_suppresses_the_content_hint_edge():
    """同一对子既有权威关系又有正文提示 -> 只画权威那条 (不重复报同一件事)。"""
    a = kp("kp-a", "Alfa", relations=["Beta"])
    b = kp("kp-b", "Beta")
    without = graph([a, b])
    assert without["stats"]["content_hint_edge_count"] == 1
    with_relation = graph([a, b], relations=[rel("kp-a", "kp-b", "related")])
    assert with_relation["stats"]["registered_relation_count"] == 1
    assert with_relation["stats"]["content_hint_edge_count"] == 0
    assert len(with_relation["edges"]) == 1
    assert with_relation["edges"][0]["origin"] == "registered"


def test_unknown_relation_endpoint_is_counted_not_drawn():
    """端点不在本图 (跨课 / 已注销) -> 不画, 但计入 dropped, 不静默。"""
    a = kp("kp-a", "Alfa", content="x")
    result = graph([a], relations=[rel("kp-a", "kp-inexistent")])
    assert result["edges"] == []
    assert result["stats"]["dropped_relation_count"] == 1
    assert result["stats"]["registered_relation_count"] == 0


def test_self_relation_from_outside_is_counted_not_drawn():
    a = kp("kp-a", "Alfa", content="x")
    result = graph([a], relations=[rel("kp-a", "kp-a")])
    assert result["edges"] == []
    assert result["stats"]["dropped_relation_count"] == 1


def test_relations_input_does_not_mutate_the_caller_data():
    """只读投影: 输入列表与其中字典都不许被原地改。"""
    import copy

    a = kp("kp-a", "Alfa", content="x")
    b = kp("kp-b", "Beta", content="x")
    passed = [rel("kp-a", "kp-b")]
    snapshot = copy.deepcopy(passed)
    graph([a, b], relations=passed)
    assert passed == snapshot


def test_output_stays_deterministic_with_registered_relations():
    points = [kp("kp-a", "Alfa", relations=["Beta"]), kp("kp-b", "Beta")]
    relations = [rel("kp-a", "kp-b", "contrasts")]
    first = json.dumps(graph(points, relations=relations), ensure_ascii=False, sort_keys=True)
    second = json.dumps(graph(points, relations=relations), ensure_ascii=False, sort_keys=True)
    assert first == second


def test_content_hints_and_concepts_still_work_without_any_registered_relation():
    """兼容路径的红线: 没有任何权威关系时, 旧行为一个字节不变。"""
    term = "concepte compartit"
    a = kp("kp-a", "Alfa", relations=[term, "Beta"])
    b = kp("kp-b", "Beta", relations=[term])
    result = graph([a, b], relations=[])
    assert result["stats"]["registered_relation_count"] == 0
    assert result["stats"]["content_hint_edge_count"] == 1
    assert result["stats"]["concept_edge_count"] == 2
    assert result["stats"]["linked_knowledge_point_count"] == 2
