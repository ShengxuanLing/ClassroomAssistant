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
