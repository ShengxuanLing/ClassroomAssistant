# -*- coding: utf-8 -*-
"""迁移 001 —— 初始 schema。

设计取舍: **规范列 + 权威 payload**
--------------------------------------------------------------------

每张表都有两种信息:

1. **规范列** —— 身份、外键、以及需要被查询/排序/约束的字段
   (``course_id`` / ``language`` / ``status`` / ``sequence`` ...)。
   它们让"关系"成为数据库能自己保证的事: 外键、唯一约束、索引。
2. **``payload`` 列** —— 领域对象自己 ``to_dict()`` 出来的 JSON。

为什么不把每个字段都拆成列? 因为这个项目的核心不变量是
**"绝不丢任何信息"**: 溯源 (``SourceReference`` 的
``timestamp_start``/``page``/``line``/``paragraph``)、``metadata`` 这类
开放字典、以及将来新增的领域字段, 只要拆列就一定会漏。用领域自己的
``to_dict()`` 作为权威表示, 可以保证"读回来和写进去逐字节一致"。

为什么不只用 JSON? 因为那样就退化成"把文件塞进数据库" ——
外键、唯一性、去重、索引全部消失, 而规范明确要求事务、幂等、
provenance 关系不丢。规范列负责**关系与约束**, payload 负责**完整性**。

两者的一致性由仓储保证: 规范列从领域对象派生, 绝不手写。

``payload_version``
--------------------------------------------------------------------

每张表带 ``payload_version``, 记录该行 payload 的序列化版本。
领域模型将来变化时可以据此识别旧行并迁移, 而不是"猜着反序列化"。
"""

from __future__ import annotations

from src.persistence.migrations import Migration

# ----------------------------------------------------------------------
# 课程 / 课堂
# ----------------------------------------------------------------------

_COURSE_TABLES = (
    """
    CREATE TABLE courses (
        course_id       TEXT PRIMARY KEY,
        name            TEXT NOT NULL DEFAULT '',
        code            TEXT NOT NULL DEFAULT '',
        language        TEXT NOT NULL DEFAULT 'Unknown',
        payload         TEXT NOT NULL,
        payload_version INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE sessions (
        session_id      TEXT PRIMARY KEY,
        course_id       TEXT NOT NULL,
        session_number  INTEGER NOT NULL DEFAULT 0,
        date            TEXT NOT NULL DEFAULT '',
        title           TEXT NOT NULL DEFAULT '',
        payload         TEXT NOT NULL,
        payload_version INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_sessions_course ON sessions(course_id, session_number)",
)

# ----------------------------------------------------------------------
# 材料 + 处理状态 (规范点名的 atomic 对)
# ----------------------------------------------------------------------

_MATERIAL_TABLES = (
    """
    CREATE TABLE materials (
        material_id     TEXT PRIMARY KEY,
        course_id       TEXT,
        session_id      TEXT,
        filename        TEXT NOT NULL DEFAULT '',
        extension       TEXT NOT NULL DEFAULT '',
        size            INTEGER NOT NULL DEFAULT 0,
        content_hash    TEXT,
        material_type   TEXT NOT NULL DEFAULT 'text',
        language        TEXT NOT NULL DEFAULT '',
        source_type     TEXT NOT NULL DEFAULT '',
        created_at      TEXT,
        stored_path     TEXT,
        relative_path   TEXT,
        payload         TEXT NOT NULL,
        payload_version INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX idx_materials_course ON materials(course_id)",
    "CREATE INDEX idx_materials_hash ON materials(content_hash)",
    "CREATE UNIQUE INDEX idx_materials_course_filename_hash "
    "ON materials(course_id, filename, content_hash)",
    # 处理状态单独一张表: 规范要求 "Material registration + processing
    # status 不能写一半"。拆成两行写入, 才真正需要一个事务来保证原子性;
    # 若塞进同一行, "原子" 就成了同义反复, 也就无从测试。
    """
    CREATE TABLE material_processing (
        material_id       TEXT PRIMARY KEY,
        processing_status TEXT NOT NULL,
        attempts          INTEGER NOT NULL DEFAULT 0,
        error             TEXT,
        error_detail      TEXT,
        warning           TEXT,
        retryable         INTEGER NOT NULL DEFAULT 0,
        payload           TEXT NOT NULL,
        payload_version   INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (material_id) REFERENCES materials(material_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_material_processing_status "
    "ON material_processing(processing_status)",
)

# ----------------------------------------------------------------------
# 证据 (内容寻址)
# ----------------------------------------------------------------------

_EVIDENCE_TABLES = (
    """
    CREATE TABLE evidence (
        evidence_id     TEXT PRIMARY KEY,
        canonical_key   TEXT NOT NULL UNIQUE,
        material_id     TEXT,
        language        TEXT NOT NULL DEFAULT 'Unknown',
        confidence      TEXT NOT NULL DEFAULT 'UNCERTAIN',
        evidence_type   TEXT NOT NULL DEFAULT 'other',
        state           TEXT NOT NULL DEFAULT 'ACTIVE',
        content         TEXT NOT NULL,
        insertion_seq   INTEGER NOT NULL DEFAULT 0,
        payload         TEXT NOT NULL,
        payload_version INTEGER NOT NULL DEFAULT 1
    )
    """,
    # canonical_key 上的 UNIQUE 约束是 Task 23 去重身份的**数据库级**保证:
    # 即使仓储被绕过, 同一 (来源 + 位置 + 内容) 也不可能插入两条。
    "CREATE INDEX idx_evidence_material ON evidence(material_id)",
    "CREATE INDEX idx_evidence_state ON evidence(state)",
    "CREATE INDEX idx_evidence_seq ON evidence(insertion_seq)",
    # 注意: evidence.material_id **故意不加外键**。证据是内容寻址的,
    # 其溯源可以指向一个尚未注册 (或来自测试/外部导入) 的 material_id;
    # 硬外键会拒掉合法证据。material_id + 完整 source_reference 都保存在
    # payload 里, 溯源不丢。
    """
    CREATE TABLE material_evidence (
        material_id TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        position    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (material_id, evidence_id),
        FOREIGN KEY (material_id) REFERENCES materials(material_id) ON DELETE CASCADE,
        FOREIGN KEY (evidence_id) REFERENCES evidence(evidence_id) ON DELETE CASCADE
    )
    """,
)

# ----------------------------------------------------------------------
# 知识: 知识点 / 溯源链 / 关系 / 冲突 / 评审历史
# ----------------------------------------------------------------------

_KNOWLEDGE_TABLES = (
    """
    CREATE TABLE knowledge_points (
        knowledge_id      TEXT PRIMARY KEY,
        course_id         TEXT,
        title             TEXT NOT NULL DEFAULT '',
        importance        TEXT NOT NULL DEFAULT 'medium',
        confidence        TEXT NOT NULL DEFAULT 'UNCERTAIN',
        validation_status TEXT NOT NULL DEFAULT 'unverified',
        review_status     TEXT NOT NULL DEFAULT 'pending',
        knowledge_score   REAL NOT NULL DEFAULT 0.0,
        needs_verification INTEGER NOT NULL DEFAULT 0,
        payload           TEXT NOT NULL,
        payload_version   INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX idx_kp_course ON knowledge_points(course_id)",
    "CREATE INDEX idx_kp_review_status ON knowledge_points(review_status)",
    # 溯源链: KnowledgePoint -> Evidence。规范要求 "不得丢 Evidence provenance",
    # 这里用外键把"悬挂引用"变成数据库拒绝的事, 而不是靠人记得检查。
    """
    CREATE TABLE knowledge_point_evidence (
        knowledge_id TEXT NOT NULL,
        evidence_id  TEXT NOT NULL,
        position     INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (knowledge_id, evidence_id),
        FOREIGN KEY (knowledge_id) REFERENCES knowledge_points(knowledge_id) ON DELETE CASCADE,
        FOREIGN KEY (evidence_id) REFERENCES evidence(evidence_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE relationships (
        relationship_id TEXT PRIMARY KEY,
        source_id       TEXT NOT NULL,
        target_id       TEXT NOT NULL,
        relation_type   TEXT NOT NULL,
        payload         TEXT NOT NULL,
        payload_version INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (source_id) REFERENCES knowledge_points(knowledge_id) ON DELETE CASCADE,
        FOREIGN KEY (target_id) REFERENCES knowledge_points(knowledge_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_relationships_source ON relationships(source_id)",
    "CREATE INDEX idx_relationships_target ON relationships(target_id)",
    "CREATE INDEX idx_relationships_type ON relationships(relation_type)",
    """
    CREATE TABLE conflicts (
        conflict_id     TEXT PRIMARY KEY,
        description     TEXT NOT NULL DEFAULT '',
        status          TEXT NOT NULL DEFAULT 'PENDING',
        payload         TEXT NOT NULL,
        payload_version INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE conflict_evidence (
        conflict_id TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        position    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (conflict_id, evidence_id),
        FOREIGN KEY (conflict_id) REFERENCES conflicts(conflict_id) ON DELETE CASCADE,
        FOREIGN KEY (evidence_id) REFERENCES evidence(evidence_id) ON DELETE CASCADE
    )
    """,
    # 评审历史: 只追加, 绝不覆盖。review_id 是主键 -> 重复提交天然幂等。
    # 列名与 ReviewRecord 对齐: 它带的是 ``decision`` (CONFIRM/REJECT/
    # KEEP_UNVERIFIED), **没有** status 字段 —— 不造一个领域里不存在的列。
    """
    CREATE TABLE review_records (
        review_id          TEXT PRIMARY KEY,
        knowledge_point_id TEXT NOT NULL,
        decision           TEXT,
        payload            TEXT NOT NULL,
        payload_version    INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX idx_review_kp ON review_records(knowledge_point_id)",
)

# ----------------------------------------------------------------------
# 知识组织层: 主题 / 归属 / 关系图
# ----------------------------------------------------------------------

_ORGANIZATION_TABLES = (
    """
    CREATE TABLE topics (
        topic_id        TEXT PRIMARY KEY,
        course_id       TEXT NOT NULL,
        parent_topic_id TEXT,
        name            TEXT NOT NULL DEFAULT '',
        payload         TEXT NOT NULL,
        payload_version INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX idx_topics_course ON topics(course_id)",
    """
    CREATE TABLE knowledge_memberships (
        membership_id     TEXT PRIMARY KEY,
        topic_id          TEXT NOT NULL,
        knowledge_point_id TEXT NOT NULL,
        payload           TEXT NOT NULL,
        payload_version   INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (topic_id) REFERENCES topics(topic_id) ON DELETE CASCADE,
        FOREIGN KEY (knowledge_point_id) REFERENCES knowledge_points(knowledge_id) ON DELETE CASCADE
    )
    """,
    "CREATE UNIQUE INDEX idx_kp_membership_unique "
    "ON knowledge_memberships(topic_id, knowledge_point_id)",
    """
    CREATE TABLE session_memberships (
        membership_id      TEXT PRIMARY KEY,
        session_id         TEXT NOT NULL,
        knowledge_point_id TEXT NOT NULL,
        payload            TEXT NOT NULL,
        payload_version    INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE,
        FOREIGN KEY (knowledge_point_id) REFERENCES knowledge_points(knowledge_id) ON DELETE CASCADE
    )
    """,
    "CREATE UNIQUE INDEX idx_session_membership_unique "
    "ON session_memberships(session_id, knowledge_point_id)",
    """
    CREATE TABLE knowledge_relations (
        relation_id                TEXT PRIMARY KEY,
        course_id                  TEXT NOT NULL,
        source_knowledge_point_id  TEXT NOT NULL,
        target_knowledge_point_id  TEXT NOT NULL,
        relation_type              TEXT NOT NULL,
        payload                    TEXT NOT NULL,
        payload_version            INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (source_knowledge_point_id) REFERENCES knowledge_points(knowledge_id) ON DELETE CASCADE,
        FOREIGN KEY (target_knowledge_point_id) REFERENCES knowledge_points(knowledge_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_relations_course ON knowledge_relations(course_id)",
    "CREATE INDEX idx_relations_source ON knowledge_relations(source_knowledge_point_id)",
    "CREATE INDEX idx_relations_target ON knowledge_relations(target_knowledge_point_id)",
    "CREATE INDEX idx_relations_type ON knowledge_relations(relation_type)",
)

# ----------------------------------------------------------------------
# 学生 / 学习事件 / 学习状态
# ----------------------------------------------------------------------

_STUDENT_TABLES = (
    """
    CREATE TABLE students (
        student_id      TEXT PRIMARY KEY,
        course_id       TEXT,
        display_name    TEXT,
        payload         TEXT NOT NULL,
        payload_version INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX idx_students_course ON students(course_id)",
    # 学习事件: (student, course, kp, sequence) 唯一 -> 重复记录同一事件
    # 天然幂等, 与 Task 30 的确定性 event_id 形成双重保证。
    # 注意: LearningEvent **没有** is_correct 字段 (对错记在状态记录的
    # correct/incorrect 计数里), 因此这里不造那个列。
    """
    CREATE TABLE learning_events (
        event_id           TEXT PRIMARY KEY,
        student_id         TEXT NOT NULL,
        course_id          TEXT NOT NULL,
        knowledge_point_id TEXT NOT NULL,
        event_type         TEXT NOT NULL,
        sequence           INTEGER NOT NULL DEFAULT 0,
        payload            TEXT NOT NULL,
        payload_version    INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE
    )
    """,
    "CREATE UNIQUE INDEX idx_learning_event_unique "
    "ON learning_events(student_id, course_id, knowledge_point_id, sequence)",
    "CREATE INDEX idx_learning_event_student ON learning_events(student_id, course_id)",
    # 学生状态: Task 30 的状态机结果 (StudentKnowledgeRecord)。
    # 规范要求 "不得丢 student state" —— 计数也一并存列, 便于查询与审计。
    """
    CREATE TABLE student_knowledge_state (
        student_id         TEXT NOT NULL,
        course_id          TEXT NOT NULL,
        knowledge_point_id TEXT NOT NULL,
        state              TEXT NOT NULL,
        first_seen_at      INTEGER NOT NULL DEFAULT 0,
        last_activity_at   INTEGER NOT NULL DEFAULT 0,
        exposure_count     INTEGER NOT NULL DEFAULT 0,
        practice_count     INTEGER NOT NULL DEFAULT 0,
        answer_count       INTEGER NOT NULL DEFAULT 0,
        correct_count      INTEGER NOT NULL DEFAULT 0,
        incorrect_count    INTEGER NOT NULL DEFAULT 0,
        payload            TEXT NOT NULL,
        payload_version    INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (student_id, course_id, knowledge_point_id),
        FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_student_state ON student_knowledge_state(course_id, state)",
)

# ----------------------------------------------------------------------
# 练习 / 作答 / 评估
# ----------------------------------------------------------------------

_EXERCISE_TABLES = (
    """
    CREATE TABLE exercises (
        exercise_id     TEXT PRIMARY KEY,
        course_id       TEXT NOT NULL,
        exercise_type   TEXT NOT NULL,
        difficulty      TEXT,
        payload         TEXT NOT NULL,
        payload_version INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX idx_exercises_course ON exercises(course_id)",
    "CREATE INDEX idx_exercises_type ON exercises(exercise_type)",
    # 规范点名: "不得丢 exercise/evaluation relation"。
    """
    CREATE TABLE exercise_knowledge_points (
        exercise_id  TEXT NOT NULL,
        knowledge_id TEXT NOT NULL,
        position     INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (exercise_id, knowledge_id),
        FOREIGN KEY (exercise_id) REFERENCES exercises(exercise_id) ON DELETE CASCADE,
        FOREIGN KEY (knowledge_id) REFERENCES knowledge_points(knowledge_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE exercise_evidence (
        exercise_id TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        position    INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (exercise_id, evidence_id),
        FOREIGN KEY (exercise_id) REFERENCES exercises(exercise_id) ON DELETE CASCADE,
        FOREIGN KEY (evidence_id) REFERENCES evidence(evidence_id) ON DELETE CASCADE
    )
    """,
    # 作答: answer_id 是内容寻址的 -> 主键即幂等键。
    """
    CREATE TABLE student_answers (
        answer_id       TEXT PRIMARY KEY,
        student_id      TEXT NOT NULL,
        course_id       TEXT,
        exercise_id     TEXT NOT NULL,
        sequence        INTEGER NOT NULL DEFAULT 0,
        submitted_value TEXT NOT NULL DEFAULT '',
        submitted_at    TEXT,
        payload         TEXT NOT NULL,
        payload_version INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE,
        FOREIGN KEY (exercise_id) REFERENCES exercises(exercise_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_answers_student ON student_answers(student_id, course_id)",
    "CREATE INDEX idx_answers_exercise ON student_answers(exercise_id)",
    """
    CREATE TABLE evaluation_results (
        evaluation_id     TEXT PRIMARY KEY,
        answer_id         TEXT NOT NULL,
        exercise_id       TEXT,
        student_id        TEXT,
        status            TEXT NOT NULL,
        score             REAL NOT NULL DEFAULT 0.0,
        evaluator_version TEXT,
        payload           TEXT NOT NULL,
        payload_version   INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (answer_id) REFERENCES student_answers(answer_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX idx_evaluation_answer ON evaluation_results(answer_id)",
    "CREATE INDEX idx_evaluation_student ON evaluation_results(student_id)",
)

# ----------------------------------------------------------------------
# 学习计划 / 学习路径
# ----------------------------------------------------------------------

_PLAN_TABLES = (
    """
    CREATE TABLE study_plans (
        plan_id         TEXT PRIMARY KEY,
        student_id      TEXT,
        course_id       TEXT,
        payload         TEXT NOT NULL,
        payload_version INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE INDEX idx_study_plans_student ON study_plans(student_id, course_id)",
    # LearningPath 领域对象**没有** path_id —— 它是一条由
    # (课程, 学生, 目标知识点) 唯一确定的派生视图。因此主键就用这个自然键,
    # 而不是另造一个存储层 id (那会引入一个领域里不存在的身份)。
    """
    CREATE TABLE learning_paths (
        course_id                  TEXT NOT NULL,
        student_id                 TEXT NOT NULL,
        target_knowledge_point_id  TEXT NOT NULL,
        status                     TEXT NOT NULL DEFAULT '',
        payload                    TEXT NOT NULL,
        payload_version            INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (course_id, student_id, target_knowledge_point_id)
    )
    """,
    "CREATE INDEX idx_learning_paths_target "
    "ON learning_paths(course_id, target_knowledge_point_id)",
)

MIGRATION_001 = Migration(
    version=1,
    name="initial_schema",
    statements=(
        _COURSE_TABLES
        + _MATERIAL_TABLES
        + _EVIDENCE_TABLES
        + _KNOWLEDGE_TABLES
        + _ORGANIZATION_TABLES
        + _STUDENT_TABLES
        + _EXERCISE_TABLES
        + _PLAN_TABLES
    ),
)
