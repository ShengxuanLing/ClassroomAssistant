# -*- coding: utf-8 -*-
"""知识查询 / 覆盖 / 缺口 / 依赖 服务 (Task 34)。

把 KnowledgeOrganizationService + KnowledgeCoverageAnalyzer +
KnowledgeDependencyAnalyzer + KnowledgeReviewService 组合成稳定的
只读查询入口。不创建新事实, 只做投影。

重复调用返回相同的 DTO 结构 (确定性)。
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from src.models import Course, Evidence, KnowledgePoint
from src.knowledge_organization import (
    CourseKnowledgeStructure,
    KnowledgeOrganizationService,
)
from src.knowledge_coverage import (
    CourseGapReport,
    KnowledgeCoverageAnalyzer,
)
from src.knowledge_dependency import (
    DependencyAnalysis,
    KnowledgeDependencyAnalyzer,
)
from src.knowledge_review import KnowledgeReviewService
from src.application.dto import (
    conflict_to_dict,
    evidence_to_dict,
    knowledge_point_to_dict,
    review_record_to_dict,
)
from src.application.errors import (
    InvalidInputError,
    NotFoundError,
)

__all__ = ["KnowledgeService", "ReviewService"]


def _require_nonempty_str(value: Any, field_name: str) -> str:
    if value is None:
        raise InvalidInputError(f"{field_name} is required")
    if not isinstance(value, str):
        raise InvalidInputError(f"{field_name} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise InvalidInputError(f"{field_name} must be a non-empty string")
    return stripped


class KnowledgeService:
    """知识查询 / 覆盖 / 缺口 / 依赖 的统一入口。

    状态 (组织服务 / 覆盖分析器 / 依赖分析器) 在构造时注入,
    应用层负责把它们绑定到同一 Course 上。
    """

    def __init__(
        self,
        service: KnowledgeOrganizationService,
        store: Any = None,
    ) -> None:
        self._service = service
        self._store = store  # EvidenceStore 或 None

    # ------------------------------------------------------------------
    # 内部索引 (通过公开 API 访问组织服务的知识快照)
    # ------------------------------------------------------------------

    def _all_kps(self) -> List[Any]:
        """所有已注册 KP (确定性: 按 knowledge_id 升序)。"""
        kps = [
            self._service._kp_by_id[kpid]
            for kpid in sorted(self._service.registered_knowledge_point_ids)
        ]
        return kps

    def _kp_dict(self) -> Dict[str, Any]:
        return {kp.knowledge_id: kp for kp in self._all_kps()}

    def _conflict_ids_by_kp(self) -> Dict[str, List[str]]:
        """每个 KP 被哪些**未解决**冲突触及 (按 conflict_id 升序)。

        判据与 ``get_conflicts`` / ``KnowledgeReviewService`` **完全一致**:
        ``conflict.evidence_refs ∩ kp.evidence_refs`` 非空。冲突归属领域层
        不记录, 只能这样推导 —— 两处口径若分叉, 会出现"冲突列表里有一条、
        但知识点上判不出来"的自相矛盾。

        只统计**未解决**的冲突: 已 ``RESOLVED`` 的冲突不再让知识点失去
        练习资格 (否则人工解决完冲突, 知识点却还是被排除, 那条修复路径
        等于白走)。
        """
        out: Dict[str, List[str]] = {}
        for kp in self._all_kps():
            snapshot = self._service._structure_by_kp.get(kp.knowledge_id)
            if snapshot is None:
                continue
            own_refs = {str(r) for r in (kp.evidence_refs or ()) if r}
            if not own_refs:
                continue
            ids = [
                str(c.conflict_id)
                for c in snapshot.conflicts
                if str(getattr(c.status, "value", c.status) or "").upper()
                != "RESOLVED"
                and own_refs & {str(r) for r in (c.evidence_refs or ()) if r}
            ]
            if ids:
                out[kp.knowledge_id] = sorted(set(ids))
        return out

    # ------------------------------------------------------------------
    # 只读查询
    # ------------------------------------------------------------------

    def get_knowledge_points(
        self,
        course_id: Optional[str] = None,
        session_id: Optional[str] = None,
        topic_id: Optional[str] = None,
        validation_status: Optional[str] = None,
        review_status: Optional[str] = None,
        conflict: Optional[bool] = None,
        language: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[dict[str, Any]]:
        """返回课程的 KnowledgePoint DTO 列表, 按 knowledge_id 升序。

        可通过 session_id / topic_id / validation_status / review_status /
        conflict / language / search 过滤。

        Filter parameters:
          - validation_status: "unverified" / "supported" / "conflicted"
          - review_status: "pending" / "confirmed" / "rejected" / "kept_unverified"
          - conflict: True (有冲突) / False (无冲突) / None (不筛选)
          - language: "es" / "ca" / "zh" / "en" (based on evidence languages)
          - search: case-insensitive substring on title / content / original_terms
        """
        # Task 66 修复的真实缺陷 (两个, 都属于"头号缺陷形状")
        # --------------------------------------------------
        # 1) 这一整段过滤器把 ``kp`` 当成 **dict** 在调 ``kp.get(...)``, 而
        #    ``_kp_dict()`` 返回的是 **domain 对象** ``KnowledgePoint`` ——
        #    dataclass 没有 ``.get()``。于是**只要带上任何一个过滤器**就必然
        #    抛 ``AttributeError``, 被 API 层映射成 5xx。``workspace`` 那一侧
        #    的签名接线 (Task 62 修的) 是对的, 函数体里的字段访问从来没对过,
        #    而 5 个过滤器在测试里**零覆盖**, 所以一直没人发现。
        # 2) ``conflict`` 过滤器读的 ``kp.get("conflict")`` 字段**根本不存在**
        #    (DTO 与 domain 都没有), 即便改用属性访问也恒为 False ->
        #    ``?conflict=true`` 永远返回空列表。冲突归属只能由证据 refs 推导,
        #    所以这里用 ``_conflict_ids_by_kp()`` 显式算一次。
        kp_map = self._kp_dict()
        conflict_ids = self._conflict_ids_by_kp()
        # 确定最初的 KP ID 集合
        if session_id is not None:
            kp_ids = list(self._service.get_session_knowledge_points(session_id))
        elif topic_id is not None:
            kp_ids = list(self._service.get_topic_knowledge_points(topic_id))
        else:
            kp_ids = sorted(kp_map.keys())
        # 应用过滤
        filtered_ids: List[str] = []
        for kid in kp_ids:
            kp = kp_map.get(kid)
            if kp is None:
                # session/topic 返回的 id 可能已从组织层注销; 过滤掉而不是
                # KeyError —— 与 ``_get_kp_or_none`` 的口径一致。
                continue
            # validation_status filter (两轴之一: 证据支持度)
            if validation_status is not None:
                if str(kp.validation_status) != str(validation_status):
                    continue
            # review_status filter (两轴之二: 人工审核决定) —— 与
            # validation_status **分别**判断, 绝不合并 (铁律 4)。
            if review_status is not None:
                if str(kp.review_status) != str(review_status):
                    continue
            # conflict filter (由证据 refs 交集推导, 见 _conflict_ids_by_kp)
            if conflict is not None and bool(conflict) != (kid in conflict_ids):
                continue
            # language filter (based on evidence languages)
            if language is not None:
                ev_langs: set[str] = set()
                for ref in (kp.evidence_refs or ()):
                    ev = (
                        self._store.get(ref, include_retired=True)
                        if self._store
                        else None
                    )
                    if ev is None:
                        continue
                    lang = (
                        ev.get("language")
                        if isinstance(ev, Mapping)
                        else getattr(ev, "language", None)
                    )
                    if lang:
                        ev_langs.add(str(lang))
                kp_lang = getattr(kp, "language", None)
                all_langs = ev_langs | ({str(kp_lang)} if kp_lang else set())
                if not any(
                    l.lower() == str(language).lower() for l in all_langs if l
                ):
                    continue
            # search filter (case-insensitive substring on title/content/terms)
            if search is not None:
                s = str(search).lower()
                title = str(kp.title or "").lower()
                content = str(kp.content or "").lower()
                terms = [str(t).lower() for t in (kp.original_terms or ())]
                if not (s in title or s in content or any(s in t for t in terms)):
                    continue
            filtered_ids.append(kid)
        kp_ids = filtered_ids
        kps = [kp_map[kid] for kid in kp_ids if kid in kp_map]
        return [
            knowledge_point_to_dict(
                kp, conflict=kp.knowledge_id in conflict_ids
            )
            for _, kp in sorted(
                ((kp.knowledge_id, kp) for kp in kps),
                key=lambda t: t[0],
            )
        ]

    def _all_registered_kp_ids(self) -> List[str]:
        return sorted(self._service.registered_knowledge_point_ids())

    def _get_kp_or_none(self, kp_id: str) -> Optional[Any]:
        """按 ID 取 KP, 未注册则 None (过滤掉组织层已失效的引用)。"""
        return self._kp_dict().get(kp_id)

    def _review_candidates(self) -> list:
        """通过组织服务的 KP 视图计算评审候选 (由 KnowledgeReviewService)。"""
        view = _kp_view_for_review(self._service)
        return KnowledgeReviewService().get_review_candidates(view)

    def get_knowledge_point(self, knowledge_point_id: str) -> dict[str, Any]:
        """获取单个 KnowledgePoint DTO; 不存在则 NotFoundError。

        带上 ``conflict`` 判据 (Task 66): 知识详情页与学习流程都靠它决定
        "这条知识能不能进入正式练习"。单个查询就只算这一个 KP 的冲突,
        不整个课程遍历。
        """
        kp_id = _require_nonempty_str(knowledge_point_id, "knowledge_point_id")
        kp = self._kp_dict().get(kp_id)
        if kp is None:
            raise NotFoundError(f"knowledge point {kp_id!r} not found")
        return knowledge_point_to_dict(
            kp, conflict=bool(self._conflict_ids_for_kp(kp))
        )

    def _conflict_ids_for_kp(self, kp: Any) -> List[str]:
        """单个 KP 被哪些未解决的冲突触及 (判据与 ``_conflict_ids_by_kp`` 同源)。"""
        return self._conflict_ids_by_kp().get(kp.knowledge_id, [])

    def get_course_knowledge(self, course_id: str) -> dict[str, Any]:
        """返回指定课程的知识结构概览 (来自 CourseKnowledgeSummary)。"""
        cid = _require_nonempty_str(course_id, "course_id")
        summary = self._service.get_course_summary()
        return summary.to_dict()

    def get_coverage(self, course_id: str) -> dict[str, Any]:
        """返回课程覆盖分析 DTO (由 KnowledgeCoverageAnalyzer 计算)。"""
        cid = _require_nonempty_str(course_id, "course_id")
        analyzer = KnowledgeCoverageAnalyzer(self._service)
        report = analyzer.analyze_course()
        return report.to_dict()

    def get_gaps(self, course_id: str) -> dict[str, Any]:
        """返回课程缺口报告 DTO (未分配 topic / 未覆盖 session / 冲突 / 未验证)。"""
        cid = _require_nonempty_str(course_id, "course_id")
        analyzer = KnowledgeCoverageAnalyzer(self._service)
        return {
            "course_id": cid,
            "gaps": [gap.to_dict() for gap in analyzer.analyze_gaps().gaps],
        }

    def get_dependencies(self, course_id: str) -> dict[str, Any]:
        """返回课程依赖分析 DTO (前置关系 / 环 / 深度 / 未覆盖前置)。"""
        cid = _require_nonempty_str(course_id, "course_id")
        analyzer = KnowledgeDependencyAnalyzer(self._service)
        report: DependencyAnalysis = analyzer.analyze_course()
        return report.to_dict()

    def get_conflicts(self, course_id: str) -> List[dict[str, Any]]:
        """返回指定课程的冲突记录 DTO 列表 (按 conflict_id 升序)。

        Task 45 修复的真实缺陷
        ----------------------
        旧实现按 KP 遍历, 每个 KP 都取 ``_structure_by_kp[kp]`` —— 而同一份
        装配产出的 ``KnowledgeStructure`` 会被所有 KP 共享。于是一条冲突会
        被**每个 KP 各报一次**: 5 个知识点的课程会把同一条冲突返回 5 遍,
        冲突 ID 完全相同。验收数据集第一次跑真实课堂数据就暴露了这一点。

        现在按 ``conflict_id`` 去重 (缺失 ID 时退化为内容键), 结果仍然
        确定性排序。冲突记录本身一字未改 —— 这里只是投影去重。

        知识点归属同样是**投影推导**
        ----------------------------
        领域层的 ``ConflictRecord`` 只记录"哪两条证据互相矛盾", 不记录它属于
        哪个知识点。但 UI 必须能从冲突卡片点进 Knowledge Detail, 而
        证据↔知识点的关联只存在于 ``KnowledgePoint.evidence_refs``。所以在
        这一次遍历里顺手把"证据 refs 与冲突 refs 有交集的 KP"收集起来
        (与 ``KnowledgeReviewService._allowed_evidence_refs`` 判定"该冲突触及
        这个 KP"的口径**完全一致**), 交给 ``conflict_to_dict`` 一起输出。

        收集的是**全部** KP 而非第一个: 一条冲突可以同时触及多个知识点,
        丢掉其余会是伪造的确定性。最终取排序后第一个作为跳转目标。
        """
        _require_nonempty_str(course_id, "course_id")
        unique: Dict[str, dict[str, Any]] = {}
        touched: Dict[str, set[str]] = {}
        for kp in self._all_kps():
            snap_structure = self._service._structure_by_kp.get(kp.knowledge_id)
            if snap_structure is None:
                continue
            own_refs = {str(r) for r in (kp.evidence_refs or ()) if r}
            for conflict in snap_structure.conflicts:
                dto = conflict_to_dict(conflict)
                key = str(dto.get("conflict_id") or "")
                if not key:
                    # 领域层理论上总会给 conflict_id; 真缺失时用内容键兜底,
                    # 绝不把两条不同的冲突折叠成一条。
                    key = "|".join(
                        [
                            ",".join(sorted(str(r) for r in dto.get("evidence_refs") or [])),
                            str(dto.get("description") or ""),
                        ]
                    )
                unique.setdefault(key, dto)
                c_refs = {str(r) for r in (dto.get("evidence_refs") or ()) if r}
                if own_refs & c_refs:
                    touched.setdefault(key, set()).add(kp.knowledge_id)
        for key, kp_ids in touched.items():
            unique[key]["knowledge_point_ids"] = sorted(kp_ids)
            unique[key]["knowledge_point_id"] = sorted(kp_ids)[0]
        return [unique[key] for key in sorted(unique)]

    def get_evidence_for_knowledge_point(
        self, knowledge_point_id: str
    ) -> List[dict[str, Any]]:
        """返回指定知识点的支撑证据 DTO 列表 (来自 EvidenceStore)。"""
        kp_id = _require_nonempty_str(knowledge_point_id, "knowledge_point_id")
        kp = self._kp_dict().get(kp_id)
        if kp is None:
            raise NotFoundError(f"knowledge point {kp_id!r} not found")
        if self._store is None:
            return []
        out: List[dict[str, Any]] = []
        for ref in kp.evidence_refs:
            ev = self._store.get(ref, include_retired=True)
            if ev is not None:
                out.append(evidence_to_dict(ev))
        return out

    def get_review_candidates(self, course_id: str) -> List[dict[str, Any]]:
        """返回指定课程的待审核候选 DTO 列表 (由 KnowledgeReviewService 计算)。"""
        _require_nonempty_str(course_id, "course_id")
        candidates = self._review_candidates()
        return [c.to_dict() for c in candidates]


def _kp_view_for_review(
    service: KnowledgeOrganizationService, view: Any = None
) -> Any:
    """构造 / 刷新供 KnowledgeReviewService 使用的 KP 视图。

    KnowledgeReviewService 的 confirm/reject/resolve 等决策方法操作
    KnowledgeStructure.knowledge_points / review_records / conflicts,
    而组织服务自身不持有这些容器 (KP 按 ID 索引, 冲突由 KP.validation_status
    标记)。这里把已注册 KP + 已存在的审核记录投影回 KnowledgeStructure,
    让评审层记录落在该视图上; 调用方 (AppService / Workspace) 负责把新的
    审核记录回写到组织服务的共享 structure 实例, 避免审核历史丢失。

    传入 ``view`` 时**原地刷新**该视图而不是新建: 审核记录与冲突必须跨
    调用保留, 只有 KP 集合需要跟随组织服务变化。这一点是必须的 —— 课程
    上下文创建时知识库还是空的, 若视图只投影一次, 之后所有真实知识点都
    会被审核层判为"不存在"。
    """
    from src.knowledge_structure import KnowledgeStructure

    if view is None:
        view = KnowledgeStructure()
    registered = set(service.registered_knowledge_point_ids)
    for kp_id in sorted(registered):
        view.knowledge_points[kp_id] = service._kp_by_id[kp_id]
    # 组织层已注销的 KP 不能在审核层留成幽灵引用。
    for kp_id in [k for k in view.knowledge_points if k not in registered]:
        del view.knowledge_points[kp_id]
    # Task 45 修复的真实缺陷: 冲突记录也必须投影过来。
    #
    # 审核层判断"哪些证据可以选"的依据是 ``structure.conflicts``
    # (KnowledgeReviewService._allowed_evidence_refs): 它把 KP 自己的证据与
    # **触及该 KP 的冲突里的证据**合并。视图里不带冲突时, 集合里就只剩 KP
    # 自己的证据 —— 而 resolve_conflict 恰恰要求用户选"另一侧"的证据, 于是
    # 冲突永远无法解决, 只会抛 INVALID_INPUT。
    #
    # 用真实课堂数据跑验收 (Task 45) 时, 第一次调用 resolve_conflict 就撞上了。
    for kp_id in sorted(view.knowledge_points):
        snapshot = service._structure_by_kp.get(kp_id)
        if snapshot is None:
            continue
        for conflict in snapshot.conflicts:
            view.add_conflict(conflict)  # 按 conflict_id 幂等
    return view


class ReviewService:
    """人类审核入口 (Task 34)。

    只通过 KnowledgeReviewService 记录显式决策, 不自动修改
    validation_status 或 evidence。CONFLICTED 状态绝不能自动变成
    CONFIRMED (必须通过 resolve_conflict / confirm 的显式选择)。
    """

    def __init__(self, service: KnowledgeOrganizationService) -> None:
        self._service = service
        self._review_service = KnowledgeReviewService()
        # 该视图同时承担两个角色:
        # 1) 组织服务当前 KP 集合的**视图** —— 每次访问时重新投影, 因为
        #    课程上下文创建时知识库还是空的, 而 KP 是后来才登记进来的;
        # 2) 审核记录 / 冲突的**持久容器** —— 同一个对象跨调用保留,
        #    绝不重建, 否则审核历史会丢失。
        self._review_view = _kp_view_for_review(service)

    @property
    def _review_structure(self) -> Any:
        """当前 KP 视图 (按需刷新 KP 集合, 保留审核记录)。"""
        return _kp_view_for_review(self._service, self._review_view)

    def get_review_candidates(self, course_id: str) -> List[dict[str, Any]]:
        """返回待审核候选列表 DTO (与 KnowledgeService.get_review_candidates 相同)。"""
        _require_nonempty_str(course_id, "course_id")
        candidates = self._review_service.get_review_candidates(self._review_structure)
        return [c.to_dict() for c in candidates]

    def confirm(
        self,
        knowledge_point_id: str,
        selected_evidence_ids: Sequence[str] = (),
        note: Optional[str] = None,
    ) -> dict[str, Any]:
        """确认一个知识点 (CONFLICTED 时必须显式选择证据侧)。"""
        kp_id = _require_nonempty_str(knowledge_point_id, "knowledge_point_id")
        self._ensure_kp_registered(kp_id)
        selected = list(selected_evidence_ids)
        try:
            record = self._review_service.confirm(
                self._review_structure,
                kp_id,
                selected_evidence_ids=selected,
                note=note,
            )
        except (KeyError, ValueError) as exc:
            # 区分 "未注册 KP" 与 "选择证据非法/冲突必须选择"
            if kp_id not in self._review_structure.knowledge_points:
                raise NotFoundError(
                    f"knowledge point {kp_id!r} not found", cause=exc
                )
            raise InvalidInputError(
                f"cannot confirm knowledge point {kp_id!r}: {exc}", cause=exc
            )
        return review_record_to_dict(record)

    def _ensure_kp_registered(self, kp_id: str) -> None:
        """确认 KP 已注册在组织服务中; 否则 NotFoundError (提前)。"""
        if kp_id not in self._review_structure.knowledge_points:
            raise NotFoundError(f"knowledge point {kp_id!r} not found")

    def reject(
        self,
        knowledge_point_id: str,
        note: Optional[str] = None,
    ) -> dict[str, Any]:
        """拒绝一个知识点。"""
        kp_id = _require_nonempty_str(knowledge_point_id, "knowledge_point_id")
        self._ensure_kp_registered(kp_id)
        try:
            record = self._review_service.reject(
                self._review_structure,
                kp_id,
                note=note,
            )
        except (KeyError, ValueError) as exc:
            raise NotFoundError(
                f"cannot reject knowledge point {kp_id!r}: {exc}", cause=exc
            )
        return review_record_to_dict(record)

    def keep_unverified(
        self,
        knowledge_point_id: str,
        note: Optional[str] = None,
    ) -> dict[str, Any]:
        """标记一个知识点保持未验证状态 (显式人工决策)。"""
        kp_id = _require_nonempty_str(knowledge_point_id, "knowledge_point_id")
        self._ensure_kp_registered(kp_id)
        try:
            record = self._review_service.keep_unverified(
                self._review_structure,
                kp_id,
                note=note,
            )
        except (KeyError, ValueError) as exc:
            raise NotFoundError(
                f"cannot keep-unverified knowledge point {kp_id!r}: {exc}", cause=exc
            )
        return review_record_to_dict(record)

    def resolve_conflict(
        self,
        knowledge_point_id: str,
        selected_evidence_ids: Sequence[str],
        note: Optional[str] = None,
    ) -> dict[str, Any]:
        """显式解决冲突: 选择信任哪些证据侧。

        selected_evidence_ids 必须非空, 且必须是该知识点或其冲突的
        证据 ID (由 domain 层校验)。
        """
        kp_id = _require_nonempty_str(knowledge_point_id, "knowledge_point_id")
        selected = [e for e in (selected_evidence_ids or []) if e]
        if not selected:
            raise InvalidInputError(
                "resolve_conflict requires at least one selected evidence id"
            )
        self._ensure_kp_registered(kp_id)
        try:
            record = self._review_service.resolve_conflict(
                self._review_structure,
                kp_id,
                selected_evidence_ids=selected,
                note=note,
            )
        except (KeyError, ValueError) as exc:
            raise InvalidInputError(
                f"cannot resolve conflict for knowledge point {kp_id!r}: {exc}",
                cause=exc,
            )
        return review_record_to_dict(record)

    def get_review_history(self, knowledge_point_id: str) -> List[dict[str, Any]]:
        """返回指定知识点的审核历史 (append-only, 按 review_id 升序)。"""
        kp_id = _require_nonempty_str(knowledge_point_id, "knowledge_point_id")
        records = self._review_structure.review_records_for_knowledge_point(kp_id)
        return [review_record_to_dict(r) for r in records]

    # ------------------------------------------------------------------
    # 持久化恢复 (Task 51)
    # ------------------------------------------------------------------

    @property
    def review_records(self) -> list:
        """当前审核历史 (追加式容器里的领域对象, 供持久化层写入)。"""
        return list(self._review_view.review_records)

    def restore_review_records(self, records: Sequence[Any]) -> int:
        """从 SQLite 恢复审核历史 (**幂等**, 返回新增条数)。

        审核历史是**只追加**的: ``KnowledgeStructure.add_review_record``
        按 ``review_id`` 去重, 因此重复恢复不会产生第二条记录。
        历史一旦恢复, ``get_review_history`` 返回的序列与重启前逐条一致
        (包括 PENDING -> CONFIRM -> REVIEW 这种多步历史, 不会被压平)。
        """
        added = 0
        for record in records or ():
            if record is None:
                continue
            if self._review_view.add_review_record(record):
                added += 1
        return added
