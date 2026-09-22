# -*- coding: utf-8 -*-
"""跨测试文件共享的**构造夹具**（不是断言库）。

为什么单开一个模块
--------------------
``_conflicted_structure`` 这类构造函数在多轮任务里被反复重写
（Task 66 的学习流程、Task 67 的复习集合、Task 68 的多课程隔离都要
"造一个真的有冲突的知识点"）。每复制一份就多一份走样的风险 ——
而它恰恰是**最容易写错**的那一类夹具: 写错了不会报错, 只会让
"冲突应被排除/阻塞"的断言在一个假阴性上通过。

因此这里集中一处, 三个任务共用同一份判据。

``conftest.py`` 只放 pytest 夹具（fixture）; 本模块放**普通函数**,
由测试文件显式 import —— 这样调用点看得见"我依赖哪个共享构造",
也不会因为 conftest 的自动注入而让依赖变得隐形。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

__all__ = [
    "build_conflicted_structure",
    "dynamic_i18n_prefixes",
    "WEB_DIR",
    "WEB_SOURCE_ORDER",
    "read_web_source",
]


# ---------------------------------------------------------------- 前端源码
#
# P1-6: 前端已从单个 app.js 拆成多个零构建脚本 (index.html 按下面的顺序
# <script> 引入)。对源码做静态断言的用例, 语义一直是"搜整个前端" —— 所以读
# "app.js" 时返回**按加载顺序拼接的全部前端源码**, 与拆分前对单个 app.js
# 断言等价。这样既不用改几百个调用点, 也不会因为拆分而静默失去覆盖。

#: 前端源码加载顺序 —— 必须与 ``src/web/index.html`` 里的 <script> 顺序一致。
WEB_SOURCE_ORDER = (
    "api.js",
    "i18n.js",
    "app.js",
    "views/dashboard.js",
    "views/learn.js",
    "views/review.js",
    "views/knowledge.js",
    "views/materials.js",
    "views/courses.js",
    "views/students.js",
    "views/exercises.js",
    "views/mistakes.js",
    "views/review-pack.js",
)

#: 内置 Web UI 资源目录 (``src/web``)。用路径推导, 避免本模块反向依赖
#: ``src.api.server`` —— 共享夹具模块应当保持无副作用。
WEB_DIR = Path(__file__).resolve().parents[1] / "src" / "web"


def read_web_source(name: str) -> str:
    """读取前端资源源码。

    ``name == "app.js"`` 时返回**全部前端源码**按加载顺序拼接的结果
    (拆分后 app.js 只剩核心工具 + 路由, 页面函数已移到 ``views/*.js``)。
    其余名字按原样读单个文件。
    """
    if name == "app.js":
        return "\n".join(
            (WEB_DIR / rel).read_text(encoding="utf-8") for rel in WEB_SOURCE_ORDER
        )
    return (WEB_DIR / name).read_text(encoding="utf-8")


def dynamic_i18n_prefixes() -> dict[str, tuple[str, ...]]:
    """``t('前缀.' + 变量)`` 形式的动态 key 的取值域, **从后端常量导入**。

    为什么必须导入而不是在测试里手抄
    ----------------------------------
    前端有几处按枚举值拼 key 的地方 (``t('rs.block.' + reason)`` 等)。这类
    调用的失败方式是本项目最隐蔽的一种: ``t()`` 查不到就**原样返回 key**,
    没有异常、没有日志、静态检查也抓不到 —— 页面上只是凭空多出一个
    ``rs.block.SOME_NEW_VALUE``。

    而"取值域"如果手抄进测试, 它就是一条会漂移的规则: 后端加一个新枚举值,
    抄来的元组不会自己长大, 测试依然绿。导入之后, 后端加枚举 → 展开集合
    跟着长大 → "这个 key 没有译文" 立刻报红。

    放在 ``support.py`` 而不是某一个测试文件里, 是因为有**两个**消费者
    (``test_learning_view`` 与 ``test_exercise_ui`` 各有一份 i18n 表检查)。
    同一张表抄两遍, 迟早有一遍漂移。

    本函数只 import 纯常量模块, 不构造任何对象、不碰文件系统。
    """
    from src.application.learning_workflow import (
        EXCLUDED_REVIEW_STATUSES,
        EXCLUDED_VALIDATION_STATUSES,
    )
    from src.application.mistakes_view import SUGGESTED_ACTIONS
    from src.application.multi_course import COUNT_KEYS
    from src.application.review_mode import ATTENTION_REASONS, BLOCK_REASONS
    from src.application.student_today_view import ATTENTION_KINDS

    return {
        # Task 63 学生关注区: student_today_view.ATTENTION_KINDS
        "attention.": tuple(ATTENTION_KINDS),
        # Task 65 错题中心建议动作: mistakes_view.SUGGESTED_ACTIONS
        "mk.action.": tuple(SUGGESTED_ACTIONS),
        # Task 66 学习流程的排除原因: learning_workflow 的两条排除集合
        # —— ``_candidates`` 的 excluded 字典的键就是这两个集合的元素。
        "learn.excluded.": tuple(
            sorted(EXCLUDED_VALIDATION_STATUSES | EXCLUDED_REVIEW_STATUSES)
        ),
        # Task 67 复习集合: review_mode.BLOCK_REASONS / ATTENTION_REASONS
        "rs.block.": tuple(BLOCK_REASONS),
        "rs.reason.": tuple(ATTENTION_REASONS),
        # Task 68 多课程: multi_course.COUNT_KEYS
        # 两条真相轴的标签 (val.* / rev.*) 在源码里是**逐个字面**写出的,
        # 不走拼前缀 —— 拼前缀漏译时 t() 会原样返回 key, 静态检查抓不到。
        "mc.count.": tuple(COUNT_KEYS),
    }


def build_conflicted_structure(
    kp_id: str,
    refs: Sequence[str],
    *,
    existing: Optional[Any] = None,
    title: str = "Conflicting definition",
    content: str = "Two sources disagree on this definition.",
) -> Any:
    """构造一个含"conflicted 知识点 + 对应冲突记录"的 ``KnowledgeStructure``。

    为什么必须整体构造, 不能"注册 KP 再补冲突"
    --------------------------------------------
    ``KnowledgeOrganizationService`` 里 ``_structure_by_kp`` **只**由
    ``register_knowledge_structure`` 填充; ``register_knowledge_point``
    仅写 ``_kp_by_id``。所以单独 register 出来的 KP 没有结构快照, 而冲突
    恰恰只存在于结构快照里 —— 后者是所有冲突投影
    (``KnowledgeService.get_conflicts``、``_kp_view_for_review``、
    ``review_summary``) 的唯一来源。这与产品里的真实路径一致:
    ``processing_service`` 与 ``workspace._restore_course`` 都是拿一份完整
    结构去 ``register_knowledge_structure``。

    关键语义
    --------
    领域层判定"这条冲突触及该知识点"的判据是
    ``conflict.evidence_refs ∩ kp.evidence_refs`` 非空 (见
    ``KnowledgeService.get_conflicts`` 与
    ``KnowledgeReviewService._allowed_evidence_refs``)。因此冲突的证据
    **必须取自该知识点自己的证据**, 否则冲突与知识点无法关联 ——
    用假造的 id 只会测出一个假阴性 (断言通过, 但走的是空集那条分支)。

    ``existing`` 传组织服务时, 会把该服务已注册的 KP 一并放进结构里:
    ``get_conflicts`` 按 KP 遍历取结构快照, 缺了它们会查不到既有冲突,
    新结构就变成既有课程的一个"退化视图"。
    """
    from src.integration import ConflictRecord
    from src.knowledge_structure import KnowledgeStructure
    from src.models import KnowledgePoint

    own = [str(r) for r in refs if r]
    assert len(own) >= 2, (
        "该知识点证据不足两条, 无法构造一个真实的冲突（夹具应保证至少两条）"
    )

    structure = KnowledgeStructure()
    if existing is not None:
        for registered_id in sorted(existing.registered_knowledge_point_ids):
            structure.add_knowledge_point(existing._kp_by_id[registered_id])

    structure.add_knowledge_point(
        KnowledgePoint(
            knowledge_id=kp_id,
            title=title,
            content=content,
            evidence_refs=own[:2],
        )
    )
    structure.add_conflict(
        ConflictRecord(
            conflict_id=f"conflict-{kp_id}",
            evidence_refs=own[:2],
            description=f"contradictory evidence for {kp_id}",
        )
    )
    return structure
