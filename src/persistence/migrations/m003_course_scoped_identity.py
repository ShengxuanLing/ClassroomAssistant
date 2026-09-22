# -*- coding: utf-8 -*-
"""迁移 003 —— 课程范围内的身份 (多课程隔离)。

为什么需要它
--------------------------------------------------------------------

业务 ID 是**内容寻址**的: 同样的一份讲义在两门课里会产出**同一个**
``knowledge_id``。这是设计使然 (spec 68 明说这是合法的), 不是 bug。

但 ``001`` 把这类身份当成了**全局唯一**的主键::

    knowledge_points(knowledge_id PRIMARY KEY, course_id, ...)
    students(student_id PRIMARY KEY, course_id, ...)
    review_records(review_id PRIMARY KEY, ...)        -- 无 course_id
    conflicts(conflict_id PRIMARY KEY, ...)           -- 无 course_id
    knowledge_point_evidence(knowledge_id, evidence_id)  -- 无 course_id

于是两门课写同一个 ``knowledge_id`` 时, 后写的那门会把先写的那门的行
**整体替换掉**。实测 (3 门内容相同的课): 库里最终只剩 10 行知识点而不是
30 行, 重启后每门课只能拿回 3 / 5 / 2 条 —— **一半的知识凭空消失**。
学生同理: 3 门课各注册一个 ``s-1``, 库里只剩 1 行。

这不是"顺序问题", 是主键里少了一列。

为什么是**新建表**而不是改旧表
--------------------------------------------------------------------

项目的迁移纪律是**只做加法** (``test_no_migration_drops_a_table`` /
``test_no_migration_recreates_an_existing_business_table``)。而 SQLite 里
"改主键"只能靠重建表, 重建又会触发另一条更硬的限制: 子表
(``session_memberships`` / ``knowledge_memberships`` / ``knowledge_relations``
/ ``exercise_knowledge_points`` / ``knowledge_point_evidence``) 的外键指向
``knowledge_points(knowledge_id)``; 一旦父键不再唯一, 这些外键会直接变成
``foreign key mismatch`` 运行时错误 —— 而不是可忽略的警告。

因此本迁移采取**新建课程级表 + 回填**的做法:

- 旧表保留, 继续作为**外键锚点 / 全局身份登记**: 只要某个
  ``knowledge_id`` 被任何课程写过, 子表的外键就一定成立。
- 新表带 ``course_id`` 进入主键, 是**每门课各自的真相**: 读取一律走新表。

两张表的分工写在 :mod:`src.persistence.repositories.knowledge` 的注释里,
不要让它们漂移。

旧库怎么办
--------------------------------------------------------------------

单课程的历史库可以无损回填 (``course_id`` 那一列是准的)。多课程且已经
发生过覆盖的历史库, 被覆盖掉的那部分**无法从库里恢复** —— 这正是本迁移
要修掉的事, 回填只能尽力而为。回填冲突时按证据归属推导课程 (证据优先,
不猜)。
"""

from __future__ import annotations

from src.persistence.migrations import Migration

MIGRATION_003 = Migration(
    version=3,
    name="course_scoped_identity",
    statements=(
        # --- 1. 每门课的知识点 (真正的按课程隔离的真相) ---------------
        """
        CREATE TABLE course_knowledge_points (
            course_id          TEXT NOT NULL,
            knowledge_id       TEXT NOT NULL,
            title              TEXT NOT NULL DEFAULT '',
            importance         TEXT NOT NULL DEFAULT 'medium',
            confidence         TEXT NOT NULL DEFAULT 'UNCERTAIN',
            validation_status  TEXT NOT NULL DEFAULT 'unverified',
            review_status      TEXT NOT NULL DEFAULT 'pending',
            knowledge_score    REAL NOT NULL DEFAULT 0.0,
            needs_verification INTEGER NOT NULL DEFAULT 0,
            payload            TEXT NOT NULL,
            payload_version    INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (course_id, knowledge_id)
        )
        """,
        "CREATE INDEX idx_ckp_title ON course_knowledge_points(course_id, title)",
        "CREATE INDEX idx_ckp_review ON course_knowledge_points(course_id, review_status)",
        # --- 2. 每门课的知识点 -> 证据溯源链 (有序) -------------------
        #
        # 旧链 ``knowledge_point_evidence`` 也按 knowledge_id 全局唯一,
        # 两门课共享同一个知识点时 ``replace()`` 会互相清掉对方的链 ——
        # 这正是"重启后知识点只剩一半"的直接原因。
        """
        CREATE TABLE course_knowledge_evidence (
            course_id    TEXT NOT NULL,
            knowledge_id TEXT NOT NULL,
            evidence_id  TEXT NOT NULL,
            position     INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (course_id, knowledge_id, evidence_id)
        )
        """,
        "CREATE INDEX idx_cke_evidence ON course_knowledge_evidence(evidence_id)",
        # --- 3. 每门课的学生 ------------------------------------------
        """
        CREATE TABLE course_students (
            course_id    TEXT NOT NULL,
            student_id   TEXT NOT NULL,
            display_name TEXT,
            payload      TEXT NOT NULL,
            payload_version INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (course_id, student_id)
        )
        """,
        # --- 4. 每门课的评审历史 --------------------------------------
        #
        # ``review_id`` 由 (知识点, 决定, 选中证据) 派生, 不含课程 ——
        # 两门课对同一个知识点做出同样的决定会得到同一条记录。分开存,
        # 否则"这门课确认过"会渗到另一门课里 (那是对真相轴的污染)。
        """
        CREATE TABLE course_review_records (
            course_id          TEXT NOT NULL,
            review_id          TEXT NOT NULL,
            knowledge_point_id TEXT NOT NULL,
            decision           TEXT,
            payload            TEXT NOT NULL,
            payload_version    INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (course_id, review_id)
        )
        """,
        "CREATE INDEX idx_crev_kp ON course_review_records(course_id, knowledge_point_id)",
        # --- 5. 每门课的冲突 ------------------------------------------
        #
        # ``ConflictRecord`` 领域对象没有 course_id, 所以课程归属只能由
        # 证据推导 —— 与项目"证据优先"一致, 不引入任何猜测。
        """
        CREATE TABLE course_conflicts (
            course_id    TEXT NOT NULL,
            conflict_id  TEXT NOT NULL,
            description  TEXT NOT NULL DEFAULT '',
            status       TEXT NOT NULL DEFAULT 'PENDING',
            payload      TEXT NOT NULL,
            payload_version INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (course_id, conflict_id)
        )
        """,
        "CREATE INDEX idx_cconf_status ON course_conflicts(course_id, status)",
        # --- 回填: 把旧表里能确定归属的行搬过来 ------------------------
        """
        INSERT INTO course_knowledge_points (
            course_id, knowledge_id, title, importance, confidence,
            validation_status, review_status, knowledge_score,
            needs_verification, payload, payload_version
        )
        SELECT course_id, knowledge_id, title, importance, confidence,
               validation_status, review_status, knowledge_score,
               needs_verification, payload, payload_version
        FROM knowledge_points
        WHERE course_id IS NOT NULL AND course_id <> ''
        ON CONFLICT(course_id, knowledge_id) DO NOTHING
        """,
        """
        INSERT INTO course_knowledge_evidence (course_id, knowledge_id, evidence_id, position)
        SELECT kp.course_id, link.knowledge_id, link.evidence_id, link.position
        FROM knowledge_point_evidence AS link
        JOIN knowledge_points AS kp ON kp.knowledge_id = link.knowledge_id
        WHERE kp.course_id IS NOT NULL AND kp.course_id <> ''
        ON CONFLICT(course_id, knowledge_id, evidence_id) DO NOTHING
        """,
        """
        INSERT INTO course_students (course_id, student_id, display_name, payload, payload_version)
        SELECT course_id, student_id, display_name, payload, payload_version
        FROM students
        WHERE course_id IS NOT NULL AND course_id <> ''
        ON CONFLICT(course_id, student_id) DO NOTHING
        """,
        """
        INSERT INTO course_review_records (
            course_id, review_id, knowledge_point_id, decision, payload, payload_version
        )
        SELECT kp.course_id, r.review_id, r.knowledge_point_id, r.decision,
               r.payload, r.payload_version
        FROM review_records AS r
        JOIN knowledge_points AS kp ON kp.knowledge_id = r.knowledge_point_id
        WHERE kp.course_id IS NOT NULL AND kp.course_id <> ''
        ON CONFLICT(course_id, review_id) DO NOTHING
        """,
        """
        INSERT INTO course_conflicts (
            course_id, conflict_id, description, status, payload, payload_version
        )
        SELECT m.course_id, c.conflict_id, c.description, c.status,
               c.payload, c.payload_version
        FROM conflicts AS c
        JOIN conflict_evidence AS ce ON ce.conflict_id = c.conflict_id
        JOIN material_evidence AS me ON me.evidence_id = ce.evidence_id
        JOIN materials AS m ON m.material_id = me.material_id
        WHERE m.course_id IS NOT NULL AND m.course_id <> ''
        ON CONFLICT(course_id, conflict_id) DO NOTHING
        """,
    ),
)
