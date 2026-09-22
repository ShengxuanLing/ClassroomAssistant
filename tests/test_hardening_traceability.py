# -*- coding: utf-8 -*-
"""Task 47.9 — Evidence Traceability Audit。

spec 原文::

    随机抽取至少 50 KnowledgePoints, 验证:
    KnowledgePoint -> supporting Evidence -> Material -> source location
    必须能够追溯。

规模问题: 验收夹具 (tests/fixtures/acceptance) 只有 6 份材料, 产出 15 个知识点,
达不到"至少 50"的抽样规模。因此本审计**程序化放大**数据集: 保留原始 6 份材料,
把板书 OCR 换成 80 段加泰语文本, 跑同一条真实流水线 -> 实测产出 90 个知识点。
放大只发生在输入材料上, 不碰任何产品代码。

抽样用固定种子的 ``random.Random(47)``: 既是"随机抽取", 又可复现
(项目对**产品代码**禁止 random, 测试里的确定性抽样不在此列)。
"""

from __future__ import annotations

import json
import pathlib
import random
import shutil

import pytest

from src.application.acceptance import AcceptanceHarness, ClassroomDataset
from src.application.workspace import Workspace

FIXTURE_DIR = pathlib.Path(__file__).resolve().parent / "fixtures" / "acceptance"
BOARD_OCR = "tema1-pissarra-ocr.json"

#: spec 要求的抽样下限。
SAMPLE_SIZE = 50

#: 放大后的板书段数 (原始夹具是 5 段)。
BOARD_SEGMENTS = 80

#: 抽样种子 —— 固定值, 保证审计可复现。
SAMPLE_SEED = 47


def _write_board_ocr(path: pathlib.Path, count: int) -> None:
    """把板书 OCR 扩成 ``count`` 段 (格式与原始夹具一致)。"""
    segments = []
    for index in range(count):
        segments.append(
            {
                "text": (
                    f"Concepte {index + 1}: definicio completa del concepte numero "
                    f"{index + 1} amb els termes originals corresponents."
                ),
                "confidence": 0.9,
                "page": 1 + index // 20,
                "bounding_box": {
                    "x": 30.0,
                    "y": float(32 + (index % 20) * 30),
                    "width": 840.0,
                    "height": 28.0,
                },
            }
        )
    payload = {
        "fixture": "task45-board-ocr",
        "language": "ca",
        "segments": segments,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.fixture(scope="module")
def traced(tmp_path_factory):
    """跑一次放大版流水线, 返回 (harness, report)。module 级复用, 只跑一次。"""
    root = tmp_path_factory.mktemp("traceability")
    dataset_dir = root / "dataset"
    shutil.copytree(FIXTURE_DIR, dataset_dir)
    _write_board_ocr(dataset_dir / BOARD_OCR, BOARD_SEGMENTS)

    harness = AcceptanceHarness(
        ClassroomDataset.from_directory(str(dataset_dir)),
        data_dir=str(root / "data"),
    )
    report = harness.run()
    return harness, report


def _source_has_location(source: dict) -> bool:
    """source 里至少要有一个能定位到原材料的字段。"""
    for key in ("location", "page", "line", "paragraph", "timestamp_start", "timestamp_end"):
        value = source.get(key)
        if value not in (None, "", []):
            return True
    return False


def test_enlarged_dataset_runs_the_whole_pipeline(traced) -> None:
    """放大数据集必须能跑完整条流水线 (否则下面的追溯没有意义)。"""
    _, report = traced
    assert report.all_steps_ok, f"failed steps: {report.failed_steps}"
    assert report.all_answered


def test_pipeline_produces_at_least_fifty_knowledge_points(traced) -> None:
    """抽样规模前提: 知识点总数必须 >= 50。"""
    harness, _ = traced
    kps = harness.workspace.knowledge_points(harness.course_id)
    assert len(kps) >= SAMPLE_SIZE, f"只有 {len(kps)} 个知识点, 达不到 spec 的抽样下限"


def test_sampled_knowledge_points_are_fully_traceable(traced) -> None:
    """核心审计: 随机抽 50 个知识点, 逐条走通 KP -> Evidence -> Material -> location。"""
    harness, _ = traced
    course_id = harness.course_id
    kps = harness.workspace.knowledge_points(course_id)
    sample = random.Random(SAMPLE_SEED).sample(kps, SAMPLE_SIZE)

    materials = {m["material_id"] for m in harness.workspace.list_materials(course_id)}
    assert materials, "课程里没有任何材料, 追溯链无从谈起"

    broken: list[str] = []
    for kp in sample:
        knowledge_id = kp["knowledge_id"]

        # 1. 知识点必须声明证据引用
        if not kp.get("evidence_refs"):
            broken.append(f"{knowledge_id}: 没有 evidence_refs")
            continue

        # 2. 引用必须真的能解析出 Evidence
        evidence_rows = harness.workspace.knowledge_evidence(course_id, knowledge_id)
        if not evidence_rows:
            broken.append(f"{knowledge_id}: knowledge_evidence 返回空")
            continue
        resolved = {row["evidence_id"] for row in evidence_rows}
        unresolved = [ref for ref in kp["evidence_refs"] if ref not in resolved]
        if unresolved:
            broken.append(f"{knowledge_id}: evidence_refs 无法解析 {unresolved}")
            continue

        # 3. 每条 Evidence 必须指向一份已登记的材料, 且有源位置
        for row in evidence_rows:
            source = row.get("source") or {}
            material_id = source.get("material_id")
            if material_id not in materials:
                broken.append(f"{knowledge_id}: 证据指向未登记材料 {material_id!r}")
            if not _source_has_location(source):
                broken.append(f"{knowledge_id}: 证据 {row['evidence_id']} 没有源位置")

    assert not broken, f"追溯链断裂 ({len(broken)} 处):\n" + "\n".join(broken[:10])


def test_every_knowledge_point_has_evidence_not_just_the_sample(traced) -> None:
    """抽样之外的全量检查: 一个没有证据的知识点就是一次编造。"""
    harness, _ = traced
    course_id = harness.course_id
    orphans = [
        kp["knowledge_id"]
        for kp in harness.workspace.knowledge_points(course_id)
        if not kp.get("evidence_refs")
        or not harness.workspace.knowledge_evidence(course_id, kp["knowledge_id"])
    ]
    assert not orphans, f"{len(orphans)} 个知识点没有可解析的证据: {orphans[:5]}"


def test_traceability_survives_a_real_restart(traced) -> None:
    """Task 48-55: 追溯链必须**跨进程重启**成立。

    本测试的前身是 ``test_reload_limitation_is_pinned_not_silent`` —— 它把
    "重启后课程注册表消失"这条已知局限钉成断言, 等持久化接上后自动失败。
    Task 48-55 把业务对象真正接进 SQLite 之后, 那条断言按设计失败了, 因此
    这里按 spec 55.14 的要求**改写为真正的 reload PASS 验收** (不是删除)。

    断言的是"重启后仍然能走通同一条证据链", 而不是"重启后对象还在" ——
    前者才是这个文件存在的意义。
    """
    harness, _ = traced
    course_id = harness.course_id

    # 重启前的追溯快照 (逐条 evidence_id, 顺序敏感)。
    before: dict[str, list[str]] = {}
    for kp in harness.workspace.knowledge_points(course_id):
        before[kp["knowledge_id"]] = [
            row["evidence_id"]
            for row in harness.workspace.knowledge_evidence(
                course_id, kp["knowledge_id"]
            )
        ]
    assert before, "重启前必须已有知识点, 否则断言会退化成空转"

    reloaded = Workspace(harness.data_dir)
    try:
        after_kps = reloaded.knowledge_points(course_id)
        after = {
            kp["knowledge_id"]: [
                row["evidence_id"]
                for row in reloaded.knowledge_evidence(course_id, kp["knowledge_id"])
            ]
            for kp in after_kps
        }
        assert set(after) == set(before), (
            "重启后知识点集合发生变化: "
            f"少了 {sorted(set(before) - set(after))[:5]}, "
            f"多了 {sorted(set(after) - set(before))[:5]}"
        )
        mismatched = [
            knowledge_id
            for knowledge_id, refs in before.items()
            if after[knowledge_id] != refs
        ]
        assert not mismatched, (
            f"重启后溯源链发生变化 ({len(mismatched)} 个知识点): {mismatched[:5]}"
        )

        # 每条证据仍然能解析到材料与源位置 —— 重启不得让 provenance 断链。
        materials = {m["material_id"] for m in reloaded.list_materials(course_id)}
        assert materials, "重启后材料注册表为空"
        for knowledge_id in sorted(after)[:SAMPLE_SIZE]:
            for row in reloaded.knowledge_evidence(course_id, knowledge_id):
                source = row.get("source") or {}
                assert source.get("material_id") in materials, (
                    f"{knowledge_id}: 重启后证据指向未登记材料 "
                    f"{source.get('material_id')!r}"
                )
                assert _source_has_location(source), (
                    f"{knowledge_id}: 重启后证据 {row['evidence_id']} 丢了源位置"
                )
    finally:
        reloaded.close()


def test_reload_limitation_is_pinned_not_silent(traced) -> None:
    """**反向守卫**: "重启后知识消失"这个旧结论必须保持**已被推翻**。

    这条测试的名字被 spec 55.14 点名保留 —— 但它现在断言的是相反的事实:
    旧结论已经修复, 而且**不许悄悄回退**。如果哪天有人把持久化接线拆掉,
    这里会立刻变红, 而不是让文档里的一句话静静过期。
    """
    harness, _ = traced
    reloaded = Workspace(harness.data_dir)
    try:
        # 旧结论说这里会抛 NotFoundError —— 现在必须**不**抛。
        points = reloaded.knowledge_points(harness.course_id)
        assert points, "重启后课程知识库为空 —— 持久化接线回退了"
    finally:
        reloaded.close()


def test_evidence_sources_cover_multiple_material_kinds(traced) -> None:
    """证据不能全部来自同一种材料 —— 否则"多来源交叉验证"是空话。"""
    harness, _ = traced
    course_id = harness.course_id
    kinds: set[str] = set()
    for kp in harness.workspace.knowledge_points(course_id):
        for row in harness.workspace.knowledge_evidence(course_id, kp["knowledge_id"]):
            kinds.add(str(row.get("evidence_type")))
    assert len(kinds) >= 2, f"证据来源过于单一: {kinds}"
