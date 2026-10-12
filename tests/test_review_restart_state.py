# -*- coding: utf-8 -*-
"""审核决定的重启持久化验收 (2026-10-12 P1 修复的守卫测试)。

缺陷 (修复前实测): ``POST /api/reviews/{id}/confirm`` 返回 200、审阅历史落盘,
但 ``_flush_knowledge`` 写的是 ``processing.structure`` 里的**另一个同 id 对象**,
重启后 ``review_status`` 回退 pending。根因与证据链见
``docs/review-restart-state-fix-report.md`` 与 ``temp/review_restart_repro/``。

本文件对应验收矩阵 (计划 §4):

1. 确认后重启 (P0 回归) —— HTTP + 领域两条路径, 断言在**全新 sqlite3 连接**
   与**重新打开的 Workspace** 上完成, 不信内存对象;
2. 决定类型 (P1) —— confirm / reject / keep_unverified 的终态与历史;
   非法转换明确报错且零部分写入;
3. 事务故障注入 (P1) —— 状态写入后 / 历史写入后两个注入点全量回滚;
4. 幂等与丢响应重试 (P2) —— 顺序重试、并发同请求只产生一次转换与一条历史;
   不同决定是追加式新决定 (API 无请求级幂等键的现状契约, 见报告 §P2.1);
5. 并发审阅者 (P2) —— 现状无版本号字段, 写入经 ``BEGIN IMMEDIATE`` 串行化;
   两个决定都完整落库且重启后一致;
6. 新证据开启新一轮 (P2) —— 上下文指纹变化 -> 队列重开; 旧历史保留;
   旧幂等请求 (同显式选择) 重放**不能**关闭新一轮 (投影层重新打开);
7. 存储恢复与隔离 —— ``PRAGMA integrity_check`` = ok; 两门课(同内容材料)的
   状态 / 历史 / 队列互不串线。

全部使用隔离临时 SQLite + 合成西班牙语材料 + ``FakeAIProvider`` (零出站);
不读取正式课程数据库。
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from src.api.server import create_server
from src.application.ai.provider import FakeAIProvider
from src.application.errors import InvalidInputError, NotFoundError
from src.application.workspace import Workspace

TEXT_ES = (
    "La integración por partes es un método fundamental de cálculo. "
    "La definición de integral definida es el área bajo la curva. "
    "El teorema fundamental del cálculo conecta derivación e integración. "
    "Por ejemplo, la integral de x dx es x al cuadrado sobre dos más constante."
)


# =====================================================================
# 夹具与工具
# =====================================================================


def _ws(data_dir: Path) -> Workspace:
    ws = Workspace(str(data_dir), asr_mode="mock", ocr_mode="mock")
    ws.configure_ai(enabled=True, provider=FakeAIProvider())
    return ws


def _register_text(ws: Workspace, course_id: str, text: str, filename: str) -> dict:
    fd, path = tempfile.mkstemp(suffix="-" + filename)
    with open(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    try:
        return ws.register_material(course_id, path, filename=filename)
    finally:
        try:
            import os

            os.remove(path)
        except OSError:
            pass


def _seed(ws: Workspace) -> tuple[str, dict]:
    """合成西语材料 -> 摄取 + 自动 AI (Fake) -> 返回 (course_id, 索引)。

    索引里带每个 KP 的副本分叉情况, 供测试选择**分叉路径**的夹具。
    """
    course_id = ws.create_course("Repaso Reproducción", "REP1", "es")["course_id"]
    record = _register_text(ws, course_id, TEXT_ES, "clase.txt")
    job = ws.process_material(course_id, record["material_id"])
    assert job["status"] == "SUCCEEDED"
    return course_id, str(record["material_id"])


def _kp_index(ws: Workspace, course_id: str) -> dict:
    """knowledge_id -> {status, diverged} (diverged = org/structure 副本不同对象)。"""
    ctx = ws.context(course_id)
    structure = ctx.processing.structure
    out: dict[str, dict] = {}
    for kp in ws.knowledge_points(course_id):
        kid = kp["knowledge_id"]
        org_obj = ctx.org_service._kp_by_id.get(kid)
        struct_obj = structure.knowledge_points.get(kid) if structure else None
        out[kid] = {
            "status": kp.get("review_status"),
            "diverged": org_obj is not None and org_obj is not struct_obj,
            "refs": list(kp.get("evidence_refs") or []),
        }
    return out


def _pick_pending(ws: Workspace, course_id: str, *, diverged: bool, prefix: str = "") -> str:
    index = _kp_index(ws, course_id)
    for kid, info in sorted(index.items()):
        if info["status"] == "pending" and info["diverged"] is diverged:
            if prefix and not kid.startswith(prefix):
                continue
            assert info["refs"], f"夹具前提: {kid} 必须有证据 refs"
            return kid
    raise AssertionError(
        f"夹具前提: 找不到 pending(diverged={diverged}, prefix={prefix!r}) 的知识点; "
        f"实际: {index}"
    )


def _db_path(data_dir: Path) -> Path:
    hits = sorted(data_dir.rglob("*.sqlite"))
    assert hits, f"no sqlite under {data_dir}"
    return hits[0]


def _fresh(data_dir: Path) -> sqlite3.Connection:
    """全新连接 —— 关键断言只信磁盘上已提交的字节。"""
    return sqlite3.connect(str(_db_path(data_dir)), timeout=10)


def _row(data_dir: Path, course_id: str, kid: str) -> dict:
    con = _fresh(data_dir)
    try:
        cur = con.execute(
            "SELECT review_status FROM course_knowledge_points"
            " WHERE course_id=? AND knowledge_id=?",
            (course_id, kid),
        )
        row = cur.fetchone()
        history = [
            r[0]
            for r in con.execute(
                "SELECT decision FROM course_review_records"
                " WHERE course_id=? AND knowledge_point_id=? ORDER BY review_id",
                (course_id, kid),
            )
        ]
        return {
            "exists": row is not None,
            "review_status": row[0] if row else None,
            "history": history,
        }
    finally:
        con.close()


def _http(base: str, method: str, path: str, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(base + path, method=method, data=data)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, {"raw": raw}



# =====================================================================
# 1. P0 回归: 确认后重启 (HTTP + 领域; 断言在新连接 / 新实例上)
# =====================================================================


class TestConfirmSurvivesRestart:
    def test_domain_confirm_survives_reopen_on_the_diverged_path(
        self, tmp_path
    ) -> None:
        """缺陷主路径: AI 分叉副本上的确认必须与历史一起提交并跨重启存活。"""
        ws = _ws(tmp_path / "data")
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")
            # 夹具前提: 这条 KP 的两份副本确实是不同对象 (缺陷的土壤)。
            assert _kp_index(ws, course_id)[kid]["diverged"] is True

            record = ws.review_confirm(course_id, kid)
            assert record["decision"] == "confirm"
            assert ws.knowledge_point(course_id, kid)["review_status"] == "confirmed"

            row = _row(tmp_path / "data", course_id, kid)
            assert row["review_status"] == "confirmed", (
                "提交后全新连接必须读到 confirmed (修复前这里回退 pending)"
            )
            assert row["history"] == ["confirm"]
        finally:
            ws.close()

        reopened = _ws(tmp_path / "data")
        try:
            assert reopened.knowledge_point(course_id, kid)["review_status"] == "confirmed"
            history = reopened.review_history(course_id, kid)
            assert [h["decision"] for h in history] == ["confirm"]
            queue_ids = {
                c["knowledge_point_id"] for c in reopened.review_candidates(course_id)
            }
            assert kid not in queue_ids, "已确认的知识点不得再出现在待审队列"
        finally:
            reopened.close()

    def test_http_confirm_survives_a_process_restart(self, tmp_path) -> None:
        """Cue 复现的原形状: 真实 HTTP 200 -> 关库 -> 新实例读同一 SQLite。"""
        data_dir = tmp_path / "data"
        ws = _ws(data_dir)
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")
            server = create_server(ws, port=0)
            server.start()
            try:
                base = server.url
                status, payload = _http(
                    base, "POST",
                    f"/api/reviews/{kid}/confirm?course_id={course_id}", {},
                )
                assert status == 200 and payload["success"] is True

                status2, payload2 = _http(
                    base, "GET", f"/api/knowledge/{kid}?course_id={course_id}"
                )
                assert status2 == 200
                assert payload2["data"]["review_status"] == "confirmed"

                status3, payload3 = _http(
                    base, "GET", f"/api/reviews/{kid}?course_id={course_id}"
                )
                assert status3 == 200
                assert [h["decision"] for h in payload3["data"]["history"]] == ["confirm"]
            finally:
                server.stop()

            row = _row(data_dir, course_id, kid)
            assert row["review_status"] == "confirmed"
            assert row["history"] == ["confirm"]
        finally:
            ws.close()

        reopened = _ws(data_dir)
        try:
            assert reopened.knowledge_point(course_id, kid)["review_status"] == "confirmed"
            assert [h["decision"] for h in reopened.review_history(course_id, kid)] == [
                "confirm"
            ]
            queue_ids = {
                c["knowledge_point_id"] for c in reopened.review_candidates(course_id)
            }
            assert kid not in queue_ids
        finally:
            reopened.close()

    def test_deterministic_kp_confirm_survives_reopen(self, tmp_path) -> None:
        """共享对象路径 (确定性 KP, same_object=true) 不得被对账破坏。"""
        ws = _ws(tmp_path / "data")
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=False, prefix="kp-")
            ws.review_confirm(course_id, kid)
            row = _row(tmp_path / "data", course_id, kid)
            assert row["review_status"] == "confirmed"
            assert row["history"] == ["confirm"]
        finally:
            ws.close()

        reopened = _ws(tmp_path / "data")
        try:
            assert reopened.knowledge_point(course_id, kid)["review_status"] == "confirmed"
        finally:
            reopened.close()


# =====================================================================
# 2. P1: 决定类型与非法转换
# =====================================================================


class TestDecisionTypes:
    @pytest.mark.parametrize(
        "method,expected",
        [
            ("review_confirm", "confirmed"),
            ("review_reject", "rejected"),
            ("review_keep_unverified", "kept_unverified"),
        ],
    )
    def test_each_decision_commits_its_final_status_and_history(
        self, tmp_path, method, expected
    ) -> None:
        ws = _ws(tmp_path / "data")
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")
            record = getattr(ws, method)(course_id, kid)
            expected_decision = {
                "review_confirm": "confirm",
                "review_reject": "reject",
                "review_keep_unverified": "keep_unverified",
            }[method]
            assert record["decision"] == expected_decision
            assert ws.knowledge_point(course_id, kid)["review_status"] == expected
            row = _row(tmp_path / "data", course_id, kid)
            assert row["review_status"] == expected
            assert row["history"] == [expected_decision]
        finally:
            ws.close()

        reopened = _ws(tmp_path / "data")
        try:
            assert reopened.knowledge_point(course_id, kid)["review_status"] == expected
            assert [
                h["decision"] for h in reopened.review_history(course_id, kid)
            ] == [expected_decision]
        finally:
            reopened.close()

    def test_keep_unverified_is_a_decided_state_not_pending(self, tmp_path) -> None:
        """计划 P1.2: 不得把 keep-unverified 等同于 pending。"""
        ws = _ws(tmp_path / "data")
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")
            ws.review_keep_unverified(course_id, kid)
            assert ws.knowledge_point(course_id, kid)["review_status"] == "kept_unverified"
            row = _row(tmp_path / "data", course_id, kid)
            assert row["review_status"] == "kept_unverified"
            assert row["review_status"] != "pending"
            queue_ids = {
                c["knowledge_point_id"] for c in ws.review_candidates(course_id)
            }
            assert kid not in queue_ids
        finally:
            ws.close()

    def test_unknown_knowledge_point_is_a_clear_error_with_zero_writes(
        self, tmp_path
    ) -> None:
        ws = _ws(tmp_path / "data")
        try:
            course_id, _ = _seed(ws)
            with pytest.raises(NotFoundError):
                ws.review_confirm(course_id, "kp-does-not-exist")
            con = _fresh(tmp_path / "data")
            try:
                assert con.execute(
                    "SELECT COUNT(*) FROM course_review_records WHERE course_id=?",
                    (course_id,),
                ).fetchone()[0] == 0
            finally:
                con.close()
        finally:
            ws.close()

    def test_resolve_conflict_without_selection_is_rejected_with_zero_writes(
        self, tmp_path
    ) -> None:
        ws = _ws(tmp_path / "data")
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")
            with pytest.raises(InvalidInputError):
                ws.review_resolve_conflict(course_id, kid, selected_evidence_ids=[])
            row = _row(tmp_path / "data", course_id, kid)
            assert row["review_status"] == "pending"
            assert row["history"] == []
        finally:
            ws.close()

    def test_confirming_a_conflicted_point_requires_a_selection(
        self, tmp_path
    ) -> None:
        """冲突 KP 无选择确认 -> 明确错误, 状态与历史零部分写入。"""
        ws = _ws(tmp_path / "data")
        try:
            course_id, _ = _seed(ws)
            refs = next(
                info["refs"]
                for info in _kp_index(ws, course_id).values()
                if info["refs"]
            )
            kid = "kp-conflicted-synth"
            ws.context(course_id).workflow.register_knowledge_point(
                {
                    "knowledge_id": kid,
                    "title": "Sintético en conflicto",
                    "content": "Dos evidencias se contradicen sobre la integral.",
                    "evidence_refs": list(refs),
                    "validation_status": "conflicted",
                    "review_status": "pending",
                }
            )
            with pytest.raises(InvalidInputError):
                ws.review_confirm(course_id, kid)
            # 零部分写入: 该知识点不得产生任何审阅历史行。
            con = _fresh(tmp_path / "data")
            try:
                assert con.execute(
                    "SELECT COUNT(*) FROM course_review_records"
                    " WHERE course_id=? AND knowledge_point_id=?",
                    (course_id, kid),
                ).fetchone()[0] == 0
            finally:
                con.close()
        finally:
            ws.close()


# =====================================================================
# 3. P1: 事务故障注入 —— 任一持久化步骤失败, 整个操作回滚
# =====================================================================


class _Boom(RuntimeError):
    pass


def _fail_at(point: str, ordinal: int):
    def injector(point_name: str, write_ordinal: int) -> None:
        if point_name == point and write_ordinal == ordinal:
            raise _Boom(f"injected failure at {point_name}#{write_ordinal}")

    return injector


class TestTransactionAtomicity:
    @pytest.mark.parametrize(
        "point,ordinal",
        [
            ("knowledge_structure", 1),  # 状态写入后 / 历史写入前
            ("review", 2),  # 历史写入后 / 事务提交前
        ],
    )
    def test_mid_commit_failure_rolls_back_status_history_and_idempotency(
        self, tmp_path, point, ordinal
    ) -> None:
        data_dir = tmp_path / "data"
        ws = _ws(data_dir)
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")

            ws.persistence.set_fault_injector(_fail_at(point, ordinal))
            try:
                with pytest.raises(_Boom):
                    ws.review_confirm(course_id, kid)
            finally:
                ws.persistence.set_fault_injector(None)
            assert ws.persistence.database.in_transaction is False

            # 新连接必须看到**整笔回滚**: 状态没动、历史没有、也就没有半成品幂等记录。
            row = _row(data_dir, course_id, kid)
            assert row["review_status"] == "pending"
            assert row["history"] == []

            # 故障后的重试必须安全可重入, 并且只产生一次转换与一条历史。
            record = ws.review_confirm(course_id, kid)
            assert record["decision"] == "confirm"
            row = _row(data_dir, course_id, kid)
            assert row["review_status"] == "confirmed"
            assert row["history"] == ["confirm"]
        finally:
            ws.close()

        reopened = _ws(data_dir)
        try:
            assert reopened.knowledge_point(course_id, kid)["review_status"] == "confirmed"
            assert len(reopened.review_history(course_id, kid)) == 1
        finally:
            reopened.close()


# =====================================================================
# 4. P2: 幂等与丢响应重试
# =====================================================================


class TestIdempotency:
    def test_sequential_replay_is_one_transition_and_one_history_row(
        self, tmp_path
    ) -> None:
        """顺序重试 / 丢响应后重试: 同 review_id、一条历史、状态不变。"""
        data_dir = tmp_path / "data"
        ws = _ws(data_dir)
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")
            first = ws.review_confirm(course_id, kid)
            second = ws.review_confirm(course_id, kid)
            assert second["review_id"] == first["review_id"]
            assert second["decision"] == "confirm"

            row = _row(data_dir, course_id, kid)
            assert row["review_status"] == "confirmed"
            assert row["history"] == ["confirm"]
        finally:
            ws.close()

        reopened = _ws(data_dir)
        try:
            assert len(reopened.review_history(course_id, kid)) == 1
            assert reopened.knowledge_point(course_id, kid)["review_status"] == "confirmed"
        finally:
            reopened.close()

    def test_concurrent_identical_requests_commit_exactly_once(self, tmp_path) -> None:
        """并发同请求: 写路径经 ``BEGIN IMMEDIATE`` + 进程写锁串行化;
        ``review_id`` 主键去重保证新连接与重启后都只有**一条**历史。"""
        data_dir = tmp_path / "data"
        ws = _ws(data_dir)
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")
            barrier = threading.Barrier(2)
            errors: list[BaseException] = []

            def worker() -> None:
                try:
                    barrier.wait(timeout=30)
                    ws.review_confirm(course_id, kid)
                except BaseException as exc:  # noqa: BLE001 - 收集后统一断言
                    errors.append(exc)

            threads = [threading.Thread(target=worker) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
            assert not errors, f"并发确认不得报错: {errors}"

            row = _row(data_dir, course_id, kid)
            assert row["review_status"] == "confirmed"
            assert row["history"] == ["confirm"], "并发同请求只允许一条历史"
        finally:
            ws.close()

        reopened = _ws(data_dir)
        try:
            assert len(reopened.review_history(course_id, kid)) == 1
        finally:
            reopened.close()

    def test_a_different_decision_appends_instead_of_overwriting(
        self, tmp_path
    ) -> None:
        """现状契约 (API 无请求级幂等键, 见报告 §P2.1): 不同决定是**新的**业务
        事件 —— 追加第二条历史并推进当前状态, 而不是"同键冲突"。"""
        data_dir = tmp_path / "data"
        ws = _ws(data_dir)
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")
            ws.review_confirm(course_id, kid)
            ws.review_reject(course_id, kid)

            row = _row(data_dir, course_id, kid)
            assert row["review_status"] == "rejected"
            assert sorted(row["history"]) == ["confirm", "reject"]
        finally:
            ws.close()

        reopened = _ws(data_dir)
        try:
            assert reopened.knowledge_point(course_id, kid)["review_status"] == "rejected"
            assert sorted(
                h["decision"] for h in reopened.review_history(course_id, kid)
            ) == ["confirm", "reject"]
        finally:
            reopened.close()


# =====================================================================
# 5. P2: 并发审阅者 (现状: 无版本号字段; 写入串行化, 决定全部完整落库)
# =====================================================================


class TestConcurrentReviewers:
    def test_conflicting_decisions_from_two_reviewers_never_corrupt_the_store(
        self, tmp_path
    ) -> None:
        """两个审阅者同时提交不同决定: 至少双方的历史都在、状态是其一、
        新连接与重启后一致、库完好。

        计划 §P2.2 的"陈旧版本冲突"需要版本号字段 —— 现产品**没有**该字段,
        也没有跨进程审阅者的产品契约, 因此不引入乐观锁 (见报告 §P2.2);
        这里验收的是现状语义下的不变量: 不静默丢历史、不产生半成品。
        """
        data_dir = tmp_path / "data"
        ws = _ws(data_dir)
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")
            barrier = threading.Barrier(2)
            errors: list[BaseException] = []

            def worker(method: str) -> None:
                try:
                    barrier.wait(timeout=30)
                    getattr(ws, method)(course_id, kid)
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [
                threading.Thread(target=worker, args=("review_confirm",)),
                threading.Thread(target=worker, args=("review_reject",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)
            assert not errors, f"并发决定不得报错: {errors}"

            row = _row(data_dir, course_id, kid)
            assert sorted(row["history"]) == ["confirm", "reject"], (
                "两个决定都必须完整落库 (追加式历史)"
            )
            assert row["review_status"] in ("confirmed", "rejected")
            con = _fresh(data_dir)
            try:
                assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                con.close()
            before_close = row["review_status"]
        finally:
            ws.close()

        reopened = _ws(data_dir)
        try:
            row = _row(data_dir, course_id, kid)
            assert row["review_status"] == before_close, "重启不得改变最终状态"
            assert sorted(row["history"]) == ["confirm", "reject"]
        finally:
            reopened.close()

    def test_sequential_reviewers_apply_in_order(self, tmp_path) -> None:
        """顺序审阅 (本地单用户的常态): 后一个决定推进状态, 历史累积。"""
        data_dir = tmp_path / "data"
        ws = _ws(data_dir)
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")
            ws.review_confirm(course_id, kid)
            ws.review_keep_unverified(course_id, kid)
            row = _row(data_dir, course_id, kid)
            assert row["review_status"] == "kept_unverified"
            assert sorted(row["history"]) == ["confirm", "keep_unverified"]
        finally:
            ws.close()

        reopened = _ws(data_dir)
        try:
            assert (
                reopened.knowledge_point(course_id, kid)["review_status"]
                == "kept_unverified"
            )
            assert sorted(
                h["decision"] for h in reopened.review_history(course_id, kid)
            ) == ["confirm", "keep_unverified"]
        finally:
            reopened.close()


# =====================================================================
# 6. P2: 新证据开启新一轮审核 (上下文指纹) + 旧请求重放
# =====================================================================


class TestNewEvidenceRound:
    def test_new_context_reopens_the_round_and_replay_cannot_close_it(
        self, tmp_path
    ) -> None:
        """新上下文 (指纹 V 分量翻转, 模拟新证据/新冲突经管线表达) ->
        队列重开新一轮; 旧历史原样保留; 重放旧幂等请求 (同显式选择) 不关闭
        新一轮 —— 投影层依据记录上的旧指纹重新打开; 重启后同样成立。

        为什么翻转 ``validation_status`` 而不是真的喂第二份材料:
        ``_review_context_fingerprint`` 显式包含 refs / 冲突 refs /
        validation_status / needs_verification 四个分量 (领域注释: 管线在新
        证据尚未挂到 KP refs 上时就通过这些字段表达新冲突)。翻转 S 分量与
        "新证据到达" 走**同一条**重开判据, 且确定、随 payload 持久化
        (refs 分量不可持久 —— 库里 evidence 不存在的 ref 重载时会被过滤)。
        ``needs_verification`` 分量不可用: AI review 类 KP 落库时它本来就是
        True (``ai/service.py`` 写死), 翻它等于没改指纹。
        """
        data_dir = tmp_path / "data"
        ws = _ws(data_dir)
        try:
            course_id, _ = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")
            refs = list(_kp_index(ws, course_id)[kid]["refs"])

            # 第一轮: 显式选择证据 -> review_id 与证据集解耦 (稳定幂等键)。
            first = ws.review_confirm(course_id, kid, selected_evidence_ids=refs)
            ctx = ws.context(course_id)
            records = [
                r for r in ctx.review_service.review_records
                if r.knowledge_point_id == kid
            ]
            assert len(records) == 1
            old_fp = records[0].evidence_fingerprint

            # 新上下文到达: 两份副本 (org 权威 + structure 载体) 同步翻转
            # validation 状态 (指纹的 S 分量)。
            org_kp = ctx.org_service._kp_by_id[kid]
            struct_kp = ctx.processing.structure.knowledge_points[kid]
            assert org_kp is not struct_kp, "夹具前提: 该 KP 是分叉副本"
            assert str(org_kp.validation_status) == "unverified"
            org_kp.validation_status = "supported"
            struct_kp.validation_status = "supported"

            # 1) 队列重开: 已确认的点带着新指纹回到待审。
            queue_ids = {
                c["knowledge_point_id"] for c in ws.review_candidates(course_id)
            }
            assert kid in queue_ids, "新上下文必须重开新一轮审核"
            assert ws.knowledge_point(course_id, kid)["review_status"] == "pending"

            # 旧历史可查, 指纹未被改写 (append-only, 旧轮记录原样保留)。
            records = [
                r for r in ctx.review_service.review_records
                if r.knowledge_point_id == kid
            ]
            assert len(records) == 1
            assert records[0].evidence_fingerprint == old_fp

            # 2) 重放旧幂等请求 (同 key): 同 review_id, 不新增历史。
            replay = ws.review_confirm(course_id, kid, selected_evidence_ids=refs)
            assert replay["review_id"] == first["review_id"]
            row = _row(data_dir, course_id, kid)
            assert row["history"] == ["confirm"], "重放不得追加第二条历史"

            # 3) 重放**没有**关闭新一轮: 记录上的指纹仍是旧上下文 -> 投影重开。
            queue_ids = {
                c["knowledge_point_id"] for c in ws.review_candidates(course_id)
            }
            assert kid in queue_ids, "旧幂等请求重放不得关闭新一轮审核"
        finally:
            ws.close()

        # 4) 重启: 新上下文已随结构落盘, 历史仍是那一条, 队列仍重开。
        reopened = _ws(data_dir)
        try:
            row = _row(data_dir, course_id, kid)
            assert row["history"] == ["confirm"]
            queue_ids = {
                c["knowledge_point_id"] for c in reopened.review_candidates(course_id)
            }
            assert kid in queue_ids, "重启后新一轮仍处于待审"
        finally:
            reopened.close()


# =====================================================================
# 7. 存储恢复与课程隔离
# =====================================================================


class TestStorageAndIsolation:
    def test_integrity_check_and_two_courses_do_not_cross_review_state(
        self, tmp_path
    ) -> None:
        """同内容材料在两门课产出相同知识点 id (内容寻址) —— 状态、历史、
        队列必须按 (course_id, knowledge_id) 隔离, 互不串线。"""
        data_dir = tmp_path / "data"
        ws = _ws(data_dir)
        try:
            course_a = ws.create_course("Curso A", "A1", "es")["course_id"]
            course_b = ws.create_course("Curso B", "B1", "es")["course_id"]
            for course_id in (course_a, course_b):
                record = _register_text(ws, course_id, TEXT_ES, "clase.txt")
                job = ws.process_material(course_id, record["material_id"])
                assert job["status"] == "SUCCEEDED"

            kid_a = _pick_pending(ws, course_a, diverged=True, prefix="aikp-")
            kid_b = _pick_pending(ws, course_b, diverged=True, prefix="aikp-")
            ws.review_confirm(course_a, kid_a)

            # A: 已确认 + 一条历史 + 离开队列。
            row_a = _row(data_dir, course_a, kid_a)
            assert row_a["review_status"] == "confirmed"
            assert row_a["history"] == ["confirm"]
            queue_a = {
                c["knowledge_point_id"] for c in ws.review_candidates(course_a)
            }
            assert kid_a not in queue_a

            # B: 自己的待审状态/历史/队列未被 A 影响。
            row_b = _row(data_dir, course_b, kid_b)
            assert row_b["review_status"] == "pending"
            assert row_b["history"] == []
            con = _fresh(data_dir)
            try:
                # 若两门课共享同一 knowledge_id (内容寻址), B 行也必须是 pending。
                foreign = con.execute(
                    "SELECT review_status FROM course_knowledge_points"
                    " WHERE course_id=? AND knowledge_id=?",
                    (course_b, kid_a),
                ).fetchone()
                assert foreign is None or foreign[0] == "pending"
                assert con.execute(
                    "SELECT COUNT(*) FROM course_review_records"
                    " WHERE course_id=? AND knowledge_point_id=?",
                    (course_b, kid_a),
                ).fetchone()[0] == 0
                assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                con.close()
            queue_b = {
                c["knowledge_point_id"] for c in ws.review_candidates(course_b)
            }
            assert kid_b in queue_b, "B 的待审队列不得被 A 的确认清空"
        finally:
            ws.close()

        reopened = _ws(data_dir)
        try:
            assert reopened.knowledge_point(course_a, kid_a)["review_status"] == "confirmed"
            assert reopened.knowledge_point(course_b, kid_b)["review_status"] == "pending"
            assert _row(data_dir, course_b, kid_b)["history"] == []
        finally:
            reopened.close()


# =====================================================================
# 附加守卫: 重复 AI 分析不得重置已做出的人工决定 (登记粘性)
# =====================================================================


class TestReanalysisKeepsDecisions:
    def test_reanalysis_does_not_reset_a_confirmed_status(self, tmp_path) -> None:
        """``POST /api/materials/{id}/ai-analyze`` 可重复触发; payload 自带
        ``review_status=pending``。登记收口点必须保持人工决定的粘性, 否则
        对账会把重置后的 pending 写进库 (报告 §回归面 2)。"""
        data_dir = tmp_path / "data"
        ws = _ws(data_dir)
        try:
            course_id, material_id = _seed(ws)
            kid = _pick_pending(ws, course_id, diverged=True, prefix="aikp-")
            ws.review_confirm(course_id, kid)

            report = ws.analyze_material_with_ai(course_id, material_id)
            assert report["status"] == "completed"

            assert ws.knowledge_point(course_id, kid)["review_status"] == "confirmed"
            row = _row(data_dir, course_id, kid)
            assert row["review_status"] == "confirmed"
            assert row["history"] == ["confirm"]
        finally:
            ws.close()

        reopened = _ws(data_dir)
        try:
            assert reopened.knowledge_point(course_id, kid)["review_status"] == "confirmed"
            assert len(reopened.review_history(course_id, kid)) == 1
        finally:
            reopened.close()






