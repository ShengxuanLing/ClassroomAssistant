# -*- coding: utf-8 -*-
"""仓储基类 (Task 42)。

两种基类
--------------------------------------------------------------------

:class:`DocumentRepository`
    "规范列 + 权威 payload" 表的通用 CRUD。子类只需给出 ``table`` 名,
    然后写**类型化**的领域方法 (``save_course`` / ``load_course`` ...)。

:class:`LinkRepository`
    多对多关系表 (provenance 链)。提供"按 position 有序替换"的语义 ——
    顺序是有意义的 (证据在知识点里的排列顺序), 因此不能当集合处理。

为什么 upsert 而不是 insert
--------------------------------------------------------------------

规范要求幂等: 同一个领域对象被保存两次, 结果必须和保存一次完全一样。
``INSERT ... ON CONFLICT DO UPDATE`` 让"保存"成为幂等操作, 也让重复
导入不会炸主键。注意这**不是**在掩盖重复 —— 真正的重复检测属于领域层
(例如 Task 23 的 canonical_key), 存储层只保证写操作可重复。

列名校验
--------------------------------------------------------------------

``put()`` 会校验传入的每个列名确实存在于表中。写错列名时抛
:class:`PersistenceValidationError`, 而不是让 SQLite 报一个晦涩的
"table has no column named ..." —— 更重要的是**绝不会静默丢弃**一个
调用方以为写进去了的字段。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Mapping, Optional, Sequence

from src.persistence.database import Database
from src.persistence.errors import (
    DuplicateRecordError,
    PersistenceValidationError,
    RecordNotFoundError,
)
from src.persistence.models.codec import encode_payload, decode_payload
from src.persistence.models.tables import COURSE_LINK_TABLES, LINK_TABLES, spec_for

__all__ = ["DocumentRepository", "LinkRepository", "CourseLinkRepository"]


#: SQLite 约束名 -> 结构化错误类型。约束是数据库在替我们守不变量;
#: 但它抛的是 ``sqlite3.IntegrityError``, 上层不该看到那个。
#: 这里把"数据库说了什么"翻译成"业务上意味着什么"。
_INTEGRITY_ERRORS: tuple[tuple[str, type], ...] = (
    ("UNIQUE constraint failed", DuplicateRecordError),
    ("PRIMARY KEY constraint failed", DuplicateRecordError),
    ("NOT NULL constraint failed", PersistenceValidationError),
    ("FOREIGN KEY constraint failed", PersistenceValidationError),
    ("CHECK constraint failed", PersistenceValidationError),
)

#: 批量查询一次最多绑多少个变量 (见 ``CourseLinkRepository.rights_for_many``)。
_BATCH = 500


def _translate_integrity_error(exc: sqlite3.IntegrityError, table: str) -> Exception:
    """把约束冲突翻译成结构化错误 (保留原始 SQLite 文本便于诊断)。"""
    text = str(exc)
    for needle, error_type in _INTEGRITY_ERRORS:
        if needle in text:
            return error_type(
                f"{table}: {text}", detail={"table": table}, cause=exc
            )
    return PersistenceValidationError(
        f"{table}: {text}", detail={"table": table}, cause=exc
    )


class _TableBase:
    """共享的列自省与 SQL 片段构造。"""

    table: str = ""

    def __init__(self, database: Database) -> None:
        if not isinstance(database, Database):
            raise PersistenceValidationError(
                f"database must be a Database, got {type(database).__name__}"
            )
        if not self.table:
            raise PersistenceValidationError(
                f"{type(self).__name__} must declare a table name"
            )
        self._db = database
        self._column_cache: Optional[tuple[str, ...]] = None

    @property
    def database(self) -> Database:
        return self._db

    def columns(self) -> tuple[str, ...]:
        """表的真实列名 (来自 PRAGMA, 惰性缓存)。"""
        if self._column_cache is None:
            rows = self._db.query(f"PRAGMA table_info({self.table})")
            if not rows:
                raise PersistenceValidationError(
                    f"table {self.table!r} does not exist "
                    "(did you run migrate()?)"
                )
            self._column_cache = tuple(r["name"] for r in rows)
        return self._column_cache

    def _require_columns(self, names: Iterable[str]) -> None:
        known = set(self.columns())
        unknown = sorted(set(names) - known)
        if unknown:
            raise PersistenceValidationError(
                f"unknown column(s) for table {self.table!r}: {unknown} "
                f"(known: {sorted(known)})"
            )


class DocumentRepository(_TableBase):
    """带 payload 的表的标准仓储。"""

    def __init__(self, database: Database) -> None:
        super().__init__(database)
        self._spec = spec_for(self.table)

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------

    def put(self, columns: Mapping[str, Any], payload: Any) -> None:
        """插入或更新一行 (幂等 upsert)。

        ``columns`` 必须包含全部主键列; ``payload`` 是领域对象或 dict,
        由 :func:`encode_payload` 规范化编码。
        """
        if not isinstance(columns, Mapping):
            raise PersistenceValidationError("columns must be a mapping")
        provided = dict(columns)
        missing = [c for c in self._spec.key_columns if c not in provided]
        if missing:
            raise PersistenceValidationError(
                f"missing primary key column(s) for table {self.table!r}: {missing}"
            )
        self._require_columns(provided.keys())
        if "payload" in provided:
            raise PersistenceValidationError(
                "'payload' is managed by the repository and must not be passed in"
            )

        payload_text = encode_payload(payload)
        names = sorted(provided) + ["payload", "payload_version"]
        values = [provided[n] for n in sorted(provided)]
        values.extend([payload_text, self._spec.payload_version])

        placeholders = ", ".join("?" for _ in names)
        key_cols = ", ".join(self._spec.key_columns)
        assignments = ", ".join(
            f"{n} = excluded.{n}" for n in names if n not in self._spec.key_columns
        )
        sql = (
            f"INSERT INTO {self.table} ({', '.join(names)}) VALUES ({placeholders}) "
            f"ON CONFLICT ({key_cols}) DO UPDATE SET {assignments}"
        )
        try:
            self._db.execute(sql, values)
        except sqlite3.IntegrityError as exc:
            # 注意: ON CONFLICT 只覆盖主键冲突。像 evidence.canonical_key 上的
            # UNIQUE 约束、以及外键约束, 都会走到这里 —— 那正是我们想要的:
            # 数据库拒绝写入, 仓储把它翻译成有语义的错误。
            raise _translate_integrity_error(exc, self.table) from exc

    def put_many(self, items: Iterable[tuple[Mapping[str, Any], Any]]) -> int:
        """批量 upsert; 返回写入行数。"""
        count = 0
        for columns, payload in items:
            self.put(columns, payload)
            count += 1
        return count

    def delete(self, *key_values: Any) -> bool:
        """按主键删除; 返回是否真的删掉了一行。"""
        self._require_key_arity(key_values)
        where = " AND ".join(f"{c} = ?" for c in self._spec.key_columns)
        cursor = self._db.execute(
            f"DELETE FROM {self.table} WHERE {where}", tuple(key_values)
        )
        return cursor.rowcount > 0

    def clear(self) -> int:
        """清空整表; 返回删除行数 (仅供测试/重建使用)。"""
        cursor = self._db.execute(f"DELETE FROM {self.table}")
        return int(cursor.rowcount or 0)

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------

    def get_row(self, *key_values: Any) -> Optional[sqlite3.Row]:
        self._require_key_arity(key_values)
        where = " AND ".join(f"{c} = ?" for c in self._spec.key_columns)
        return self._db.query_one(
            f"SELECT * FROM {self.table} WHERE {where}", tuple(key_values)
        )

    def get(self, *key_values: Any) -> Optional[dict[str, Any]]:
        """按主键取 payload; 不存在返回 None。"""
        row = self.get_row(*key_values)
        if row is None:
            return None
        return decode_payload(row["payload"], context=f"{self.table}.payload")

    def require(self, *key_values: Any) -> dict[str, Any]:
        """按主键取 payload; 不存在抛 :class:`RecordNotFoundError`。"""
        payload = self.get(*key_values)
        if payload is None:
            raise RecordNotFoundError(
                f"{self.table} record not found: {key_values!r}",
                detail={"table": self.table, "key": list(map(str, key_values))},
            )
        return payload

    def rows(
        self,
        *,
        where: Optional[str] = None,
        params: Sequence[Any] = (),
        order_by: Optional[str] = None,
    ) -> list[sqlite3.Row]:
        """按确定性顺序取原始行。"""
        sql = f"SELECT * FROM {self.table}"
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {order_by or self._spec.order_by}"
        return self._db.query(sql, tuple(params))

    def all(
        self,
        *,
        where: Optional[str] = None,
        params: Sequence[Any] = (),
        order_by: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """按确定性顺序取全部 payload。"""
        return [
            decode_payload(r["payload"], context=f"{self.table}.payload")
            for r in self.rows(where=where, params=params, order_by=order_by)
        ]

    def keys(
        self,
        *,
        where: Optional[str] = None,
        params: Sequence[Any] = (),
        order_by: Optional[str] = None,
    ) -> list[Any]:
        """按确定性顺序取主键列表 (单主键表)。"""
        if len(self._spec.key_columns) != 1:
            raise PersistenceValidationError(
                f"{self.table!r} has a composite key; use key_tuples()"
            )
        column = self._spec.key_columns[0]
        sql = f"SELECT {column} AS k FROM {self.table}"
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {order_by or self._spec.order_by}"
        return [r["k"] for r in self._db.query(sql, tuple(params))]

    def key_tuples(self) -> list[tuple]:
        """按确定性顺序取复合主键元组列表。"""
        cols = ", ".join(self._spec.key_columns)
        rows = self._db.query(
            f"SELECT {cols} FROM {self.table} ORDER BY {self._spec.order_by}"
        )
        return [tuple(r[c] for c in self._spec.key_columns) for r in rows]

    def count(
        self, *, where: Optional[str] = None, params: Sequence[Any] = ()
    ) -> int:
        sql = f"SELECT COUNT(*) AS n FROM {self.table}"
        if where:
            sql += f" WHERE {where}"
        return int(self._db.scalar(sql, tuple(params), default=0) or 0)

    def exists(self, *key_values: Any) -> bool:
        return self.get_row(*key_values) is not None

    def payload_text(self, *key_values: Any) -> Optional[str]:
        """原始 payload 文本 (用于逐字节一致性校验)。"""
        row = self.get_row(*key_values)
        if row is None:
            return None
        return row["payload"]

    # ------------------------------------------------------------------

    def _require_key_arity(self, key_values: Sequence[Any]) -> None:
        expected = len(self._spec.key_columns)
        if len(key_values) != expected:
            raise PersistenceValidationError(
                f"{self.table!r} expects {expected} key value(s) "
                f"({', '.join(self._spec.key_columns)}), got {len(key_values)}"
            )


class LinkRepository(_TableBase):
    """多对多关系表仓储 (有序)。

    ``replace()`` 是主要入口: 一次调用把左侧的全部右侧成员**按给定顺序**
    写回去。顺序被显式保存为 ``position``, 因此读取端能还原原始排列。
    """

    def __init__(self, database: Database, table: str) -> None:
        self.table = table
        super().__init__(database)
        if table not in LINK_TABLES:
            raise PersistenceValidationError(
                f"unknown link table: {table!r} (known: {sorted(LINK_TABLES)})"
            )
        self._left, self._right, self._order = LINK_TABLES[table]

    @property
    def left_column(self) -> str:
        return self._left

    @property
    def right_column(self) -> str:
        return self._right

    def replace(
        self,
        left_value: str,
        right_values: Sequence[str],
        *,
        only_existing: Optional[tuple[str, str]] = None,
    ) -> list[str]:
        """把 ``left_value`` 的右侧成员整体替换为 ``right_values`` (按序)。

        去重但保留**首次出现**的位置 —— 顺序是溯源信息的一部分。
        绝不产生重复行 (主键 (left, right) 保证)。

        ``only_existing`` (表名, 列名) —— 只把**确实存在**的值写进索引,
        返回被跳过的值 (按输入顺序)。

        为什么需要这个开关: 关系表是**查询索引**, 而权威数据在领域对象的
        ``payload`` 里。有些领域对象**刻意允许**悬空引用, 并且把悬空本身
        当成必须暴露给用户的数据缺陷 —— 例如 ``Exercise.evidence_ids``
        可以引用一条不存在的证据, 练习视图必须报 ``unresolved_evidence_ids``
        而不是把它藏起来 (见 tests/test_exercise_ui.py
        ``test_view_reports_broken_evidence_instead_of_hiding_it``)。

        这种情况下外键会把一个**合法的领域状态**变成写入失败, 于是持久化
        层偷偷改变了领域语义。正确做法是: 索引只索引可解析的链接, 权威
        引用列表仍然完整地留在 payload 里 —— 悬空引用照样能被报出来, 重启
        之后也照样在。

        反过来, ``only_existing=None`` (默认) 保留严格外键: 像
        "课堂必须属于一个真实存在的课程" 这种不变量, 悬空是**错误**而不是
        数据, 必须在写入时就被拒绝。
        """
        if not isinstance(left_value, str) or not left_value:
            raise PersistenceValidationError("left value must be a non-empty string")
        seen: list[str] = []
        for value in right_values or ():
            text = str(value)
            if not text or text in seen:
                continue
            seen.append(text)
        skipped: list[str] = []
        if only_existing is not None:
            seen, skipped = self._split_existing(seen, only_existing)
        self.clear_for(left_value)
        if not seen:
            return skipped
        try:
            self._db.executemany(
                f"INSERT INTO {self.table} ({self._left}, {self._right}, position) "
                "VALUES (?, ?, ?)",
                [(left_value, value, index) for index, value in enumerate(seen)],
            )
        except sqlite3.IntegrityError as exc:
            raise _translate_integrity_error(exc, self.table) from exc
        return skipped

    def _split_existing(
        self, values: Sequence[str], target: tuple[str, str]
    ) -> tuple[list[str], list[str]]:
        """把 ``values`` 拆成"在 ``target`` 表里存在的"与"不存在的"。

        一次 ``SELECT`` 完成, 不逐值查询。表名/列名来自代码常量 (调用点
        写死), 不是用户输入, 因此拼接是安全的。
        """
        table, column = target
        if not values:
            return [], []
        placeholders = ", ".join("?" for _ in values)
        rows = self._db.query(
            f"SELECT {column} FROM {table} WHERE {column} IN ({placeholders})",
            tuple(values),
        )
        present = {str(row[column]) for row in rows}
        return [v for v in values if v in present], [v for v in values if v not in present]

    def add(self, left_value: str, right_value: str) -> bool:
        """追加一个右侧成员 (已存在则不重复, 保持原位置)。"""
        if self.exists(left_value, right_value):
            return False
        next_position = int(
            self._db.scalar(
                f"SELECT COALESCE(MAX(position), -1) + 1 FROM {self.table} "
                f"WHERE {self._left} = ?",
                (left_value,),
                default=0,
            )
            or 0
        )
        try:
            self._db.execute(
                f"INSERT INTO {self.table} ({self._left}, {self._right}, position) "
                "VALUES (?, ?, ?)",
                (left_value, right_value, next_position),
            )
        except sqlite3.IntegrityError as exc:
            raise _translate_integrity_error(exc, self.table) from exc
        return True

    def remove(self, left_value: str, right_value: str) -> bool:
        cursor = self._db.execute(
            f"DELETE FROM {self.table} WHERE {self._left} = ? AND {self._right} = ?",
            (left_value, right_value),
        )
        return cursor.rowcount > 0

    def exists(self, left_value: str, right_value: str) -> bool:
        return (
            self._db.query_one(
                f"SELECT 1 FROM {self.table} WHERE {self._left} = ? AND {self._right} = ?",
                (left_value, right_value),
            )
            is not None
        )

    def rights_for(self, left_value: str) -> list[str]:
        """``left_value`` 的右侧成员, 按 position 顺序。"""
        rows = self._db.query(
            f"SELECT {self._right} AS v FROM {self.table} "
            f"WHERE {self._left} = ? ORDER BY position, {self._right}",
            (left_value,),
        )
        return [r["v"] for r in rows]

    def lefts_for(self, right_value: str) -> list[str]:
        rows = self._db.query(
            f"SELECT {self._left} AS v FROM {self.table} "
            f"WHERE {self._right} = ? ORDER BY {self._left}",
            (right_value,),
        )
        return [r["v"] for r in rows]

    def clear_for(self, left_value: str) -> int:
        cursor = self._db.execute(
            f"DELETE FROM {self.table} WHERE {self._left} = ?", (left_value,)
        )
        return int(cursor.rowcount or 0)

    def clear(self) -> int:
        cursor = self._db.execute(f"DELETE FROM {self.table}")
        return int(cursor.rowcount or 0)

    def count(self, *, where: Optional[str] = None, params: Sequence[Any] = ()) -> int:
        sql = f"SELECT COUNT(*) AS n FROM {self.table}"
        if where:
            sql += f" WHERE {where}"
        return int(self._db.scalar(sql, tuple(params), default=0) or 0)

    def distinct_lefts(self) -> list[str]:
        rows = self._db.query(
            f"SELECT DISTINCT {self._left} AS v FROM {self.table} ORDER BY {self._left}"
        )
        return [r["v"] for r in rows]

    def all_pairs(self) -> list[tuple[str, str, int]]:
        rows = self._db.query(
            f"SELECT {self._left} AS l, {self._right} AS r, position AS p "
            f"FROM {self.table} ORDER BY {self._order}"
        )
        return [(r["l"], r["r"], int(r["p"])) for r in rows]


class CourseLinkRepository(_TableBase):
    """带 ``course_id`` 的有序关系表 (Task 68)。

    为什么需要单独一个类
    --------------------

    ``LinkRepository`` 的左键只有一列。但溯源链 ``(knowledge_id, evidence_id)``
    在两门课之间会**互相清掉**: ``replace()`` 先删左键的全部行再重写, 而
    ``knowledge_id`` 是内容寻址的 —— 两门课共享它就是共享同一个左键。

    实测后果: 三门内容相同的课, 重启后每门只拿回三分之一的知识点的证据
    链, 于是"这条证据属于哪门课"的判据失效, 知识点被判给错误的课程,
    每门课凭空少掉一半知识点。

    所以左键必须是 ``(course_id, left)``。这里不复用 ``LinkRepository``
    再加一个可选参数 —— 那会让"这条链到底有没有课程维度"变成一个
    运行时的偶然, 而它必须是一个**结构上的**事实。
    """

    def __init__(self, database: Database, table: str) -> None:
        self.table = table
        super().__init__(database)
        if table not in COURSE_LINK_TABLES:
            raise PersistenceValidationError(
                f"unknown course link table: {table!r} "
                f"(known: {sorted(COURSE_LINK_TABLES)})"
            )
        self._left = COURSE_LINK_TABLES[table]
        self._right = "evidence_id"
        self._order = "course_id, " + self._left + ", position, " + self._right

    @property
    def left_column(self) -> str:
        return self._left

    @property
    def right_column(self) -> str:
        return self._right

    @staticmethod
    def _clean(values: Sequence[str]) -> list[str]:
        seen: list[str] = []
        for value in values or ():
            text = str(value)
            if text and text not in seen:
                seen.append(text)
        return seen

    @staticmethod
    def _placeholders(count: int) -> str:
        return ", ".join("?" for _ in range(count))

    def replace(
        self,
        course_id: str,
        left_value: str,
        right_values: Sequence[str],
        *,
        only_existing: Optional[tuple[str, str]] = None,
    ) -> list[str]:
        """把 ``(course_id, left_value)`` 的右侧成员整体替换 (按序)。"""
        if not isinstance(course_id, str) or not course_id:
            raise PersistenceValidationError("course_id must be a non-empty string")
        if not isinstance(left_value, str) or not left_value:
            raise PersistenceValidationError("left value must be a non-empty string")
        seen = self._clean(right_values)
        skipped: list[str] = []
        if only_existing is not None:
            seen, skipped = self._split_existing(seen, only_existing)
        self.clear_for(course_id, left_value)
        if not seen:
            return skipped
        try:
            self._db.executemany(
                f"INSERT INTO {self.table} "
                "(course_id, " + self._left + ", " + self._right + ", position) "
                "VALUES (?, ?, ?, ?)",
                [
                    (course_id, left_value, value, index)
                    for index, value in enumerate(seen)
                ],
            )
        except sqlite3.IntegrityError as exc:
            raise _translate_integrity_error(exc, self.table) from exc
        return skipped

    def _split_existing(
        self, values: Sequence[str], target: tuple[str, str]
    ) -> tuple[list[str], list[str]]:
        table, column = target
        if not values:
            return [], []
        rows = self._db.query(
            f"SELECT {column} FROM {table} "
            f"WHERE {column} IN ({self._placeholders(len(values))})",
            tuple(values),
        )
        present = {str(row[column]) for row in rows}
        return (
            [v for v in values if v in present],
            [v for v in values if v not in present],
        )

    def rights_for(self, course_id: str, left_value: str) -> list[str]:
        rows = self._db.query(
            f"SELECT {self._right} AS v FROM {self.table} "
            "WHERE course_id = ? AND " + self._left + " = ? "
            f"ORDER BY position, {self._right}",
            (course_id, left_value),
        )
        return [r["v"] for r in rows]

    def rights_for_many(
        self, course_id: str, left_values: Sequence[str]
    ) -> dict[str, list[str]]:
        """一次性取回**多个**左键的右侧成员 —— 避免 N+1。

        Task 69 实测: ``KnowledgeRepository.load_structure`` 对每个知识点
        调一次 ``rights_for``。一门课 600 个知识点就是 600 条 SQL, 五门课
        的 ``my_courses`` 冷启动 3000 条; 而且它是**每门课各查各的**, 所以
        换成一条 ``IN (...)`` 之后, 冷启动的语句条数与知识点数无关。

        分批是因为 SQLite 的 ``SQLITE_MAX_VARIABLE_NUMBER`` 在上限以下才有
        保证; 一批 500 个绑定变量离任何历史默认上限都还有余量。
        """
        values = self._clean(left_values)
        result: dict[str, list[str]] = {value: [] for value in values}
        if not values:
            return result
        for start in range(0, len(values), _BATCH):
            chunk = values[start : start + _BATCH]
            rows = self._db.query(
                f"SELECT {self._left} AS l, {self._right} AS v FROM {self.table} "
                "WHERE course_id = ? AND "
                f"{self._left} IN ({self._placeholders(len(chunk))}) "
                f"ORDER BY {self._left}, position, {self._right}",
                (course_id, *chunk),
            )
            for row in rows:
                result.setdefault(str(row["l"]), []).append(str(row["v"]))
        return result

    def lefts_for(self, course_id: Optional[str], right_value: str) -> list[str]:
        """反向查询; ``course_id`` 为 None 时跨课程查 (仅供溯源审计)。"""
        if course_id is None:
            rows = self._db.query(
                f"SELECT {self._left} AS v FROM {self.table} "
                f"WHERE {self._right} = ? ORDER BY course_id, {self._left}",
                (right_value,),
            )
        else:
            rows = self._db.query(
                f"SELECT {self._left} AS v FROM {self.table} "
                "WHERE course_id = ? AND " + self._right + " = ? "
                f"ORDER BY {self._left}",
                (course_id, right_value),
            )
        return [r["v"] for r in rows]

    def clear_for(self, course_id: str, left_value: str) -> int:
        cursor = self._db.execute(
            f"DELETE FROM {self.table} "
            "WHERE course_id = ? AND " + self._left + " = ?",
            (course_id, left_value),
        )
        return int(cursor.rowcount or 0)

    def clear_course(self, course_id: str) -> int:
        cursor = self._db.execute(
            f"DELETE FROM {self.table} WHERE course_id = ?", (course_id,)
        )
        return int(cursor.rowcount or 0)

    def count(self, *, where: Optional[str] = None, params: Sequence[Any] = ()) -> int:
        sql = f"SELECT COUNT(*) AS n FROM {self.table}"
        if where:
            sql += f" WHERE {where}"
        return int(self._db.scalar(sql, tuple(params), default=0) or 0)

    def all_pairs(self) -> list[tuple[str, str, str, int]]:
        rows = self._db.query(
            f"SELECT course_id AS c, {self._left} AS l, {self._right} AS r, "
            f"position AS p FROM {self.table} ORDER BY {self._order}"
        )
        return [
            (r["c"], r["l"], r["r"], int(r["p"])) for r in rows
        ]
