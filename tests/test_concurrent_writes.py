# -*- coding: utf-8 -*-
"""并发写冒烟测试 (P1-5)。

目标: 验证多线程对同一 SQLite 文件并发写时, 提交不丢失、WAL 语义正常、重读一致。

设计要点
--------------------------------------------------------------------
- **同一 DB 文件、各自一条连接**: 每个线程用独立的 ``Database`` / ``Repositories``
  实例打开同一个 ``.sqlite`` 文件。``Database`` 的写事务记账 (``_depth``) 是
  **实例级别**的, 不能把一个实例跨线程共享做并发事务 (否则 savepoint 编号互踩);
  真实并发模型是「多连接写同一文件」, 由 SQLite WAL + ``busy_timeout`` 协调。
- 每个线程在自己的事务里写一批 ``KnowledgePoint`` (全局表, ``course_id=None``,
  无外键依赖), 主键 ``knowledge_id`` 带线程前缀, 保证互不冲突。
- 只断「无丢失提交 + 重读一致」, **不设绝对耗时 SLA** (与现有 G 门风格一致)。
- 默认不跑 (``integration`` 标记); 需要时用 ``pytest -m integration`` 触发。

无网络、无模型、无 GPU, 纯 stdlib, 在任何环境都应稳定通过。
"""

from __future__ import annotations

import threading

import pytest

from src.models import KnowledgePoint, ValidationStatus, ReviewStatus
from src.persistence import open_database, Repositories

N_THREADS = 6
BATCH_PER_THREAD = 20


def _make_batch(thread_idx: int, n: int) -> list[KnowledgePoint]:
    kps: list[KnowledgePoint] = []
    for i in range(n):
        knowledge_id = f"kp-conc-{thread_idx:02d}-{i:03d}"
        kps.append(
            KnowledgePoint(
                knowledge_id=knowledge_id,
                title=f"Concepte {thread_idx}-{i}",
                content=f"Definició determinista del punt de coneixement {thread_idx}.{i}.",
                original_terms=[f"term-{thread_idx}-{i}"],
                importance="medium",
                confidence="high",
                evidence_refs=[],
                validation_status=ValidationStatus.SUPPORTED.value,
                review_status=ReviewStatus.PENDING.value,
                knowledge_score=0.9,
            )
        )
    return kps


def _worker(db_path: str, thread_idx: int, errors: list) -> None:
    try:
        # 每个线程独立实例 + 独立连接, 模拟多连接写同一文件。
        db = open_database(db_path)
        repos = Repositories(db)
        batch = _make_batch(thread_idx, BATCH_PER_THREAD)
        with repos.transaction():
            repos.knowledge.save_many(batch, course_id=None)
        db.close()
    except Exception as exc:  # noqa: BLE001 - 收集而非吞掉, 便于断言
        errors.append((thread_idx, repr(exc)))


@pytest.mark.integration
def test_concurrent_writes_no_lost_commits(tmp_path):
    """6 线程并发写, 最终计数 == 6*20, 且每条期望记录都能重读出来。"""
    db_path = str(tmp_path / "concurrent.sqlite")
    # 先建库并迁移, 让后续并发连接落在一致 schema 上。
    open_database(db_path).close()

    errors: list = []
    threads = [
        threading.Thread(target=_worker, args=(db_path, t, errors))
        for t in range(N_THREADS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 没有线程抛错 (写事务必须都成功提交)。
    assert errors == [], f"并发写出现错误: {errors}"

    # 重读一致性: 用一把全新的连接读, 避免连接快照残留。
    read_db = open_database(db_path)
    read_repos = Repositories(read_db)
    total = read_repos.knowledge.count()
    assert total == N_THREADS * BATCH_PER_THREAD, (
        f"并发写丢提交: 期望 {N_THREADS * BATCH_PER_THREAD}, 实际 {total}"
    )

    # 逐条核对期望 knowledge_id 都在 (WAL 重读一致)。
    stored_ids = {kp.knowledge_id for kp in read_repos.knowledge.load_all()}
    expected_ids = {
        f"kp-conc-{t:02d}-{i:03d}"
        for t in range(N_THREADS)
        for i in range(BATCH_PER_THREAD)
    }
    missing = expected_ids - stored_ids
    assert not missing, f"重读缺失 {len(missing)} 条: {sorted(missing)[:5]}"
    read_db.close()


@pytest.mark.integration
def test_concurrent_writes_idempotent_reread(tmp_path):
    """并发写完成后, 连续两次重读计数一致 (无半写 / 无幻读)。"""
    db_path = str(tmp_path / "concurrent2.sqlite")
    open_database(db_path).close()

    errors: list = []
    threads = [
        threading.Thread(target=_worker, args=(db_path, t, errors))
        for t in range(N_THREADS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], f"并发写出现错误: {errors}"

    read_db = open_database(db_path)
    read_repos = Repositories(read_db)
    first = read_repos.knowledge.count()
    second = read_repos.knowledge.count()
    assert first == second == N_THREADS * BATCH_PER_THREAD, (
        f"两次重读计数不一致: {first} vs {second}"
    )
    read_db.close()
