# -*- coding: utf-8 -*-
"""Task 42 —— 分层守卫（规范: "Domain 不应该直接依赖 SQL"）。

这一层不是功能测试, 而是**结构性**断言: 它保证"领域层不认识数据库"这件事
是代码里的事实, 而不是一句设计口号。

为什么值得单独一个文件
--------------------------------------------------------------------
因为这类约束最容易在**后续任务**里被悄悄破坏: 某天有人为了图快, 在
``src/exercises.py`` 里 ``import sqlite3`` 直接查库, 所有功能测试照样全绿,
但整个分层就塌了。静态守卫是唯一能挡住它的东西。

用 ``ast`` 解析真实 import 语句, 而不是在源码文本里搜 "sqlite3" ——
后者会被文档字符串和注释误伤 (``repositories/evidence.py`` 的 docstring 里
就写着 "不导入 sqlite3")。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"
PERSISTENCE = SRC / "persistence"


def _imported_modules(path: pathlib.Path) -> set[str]:
    """一个 .py 文件里所有 import 的**顶层模块名**。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对 import -> 相对本文件所在包
                names.add(f"<relative:{node.module or ''}>")
            elif node.module:
                names.add(node.module)
    return names


def _python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


# ======================================================================
# sqlite3 只允许出现在 src/persistence/ 内
# ======================================================================


def test_sqlite3_is_imported_only_inside_the_persistence_package():
    offenders: list[str] = []
    for path in _python_files(SRC):
        if PERSISTENCE in path.parents:
            continue
        if any(name == "sqlite3" or name.startswith("sqlite3.") for name in _imported_modules(path)):
            offenders.append(path.relative_to(SRC).as_posix())
    assert offenders == [], (
        f"sqlite3 只能出现在 src/persistence/ 内, 但这些文件也导入了它: {offenders}"
    )


def test_persistence_package_actually_uses_sqlite3():
    """反向断言 —— 否则上面那条会因为"根本没人用 sqlite3"而永远为真。"""
    users = [
        path.relative_to(SRC).as_posix()
        for path in _python_files(PERSISTENCE)
        if "sqlite3" in _imported_modules(path)
    ]
    assert users, "src/persistence/ 里没有任何模块导入 sqlite3 —— 守卫会变成空跑"


@pytest.mark.parametrize(
    "module",
    [
        "evidence_store.py",
        "knowledge_pipeline.py",
        "knowledge_structure.py",
        "knowledge_review.py",
        "knowledge_organization.py",
        "integration.py",
        "models.py",
        "exercises.py",
        "answer_evaluation.py",
        "study_plan.py",
        "student_learning.py",
    ],
)
def test_domain_modules_do_not_import_sqlite3(module):
    """领域模块一个都不许认识 SQL。"""
    path = SRC / module
    assert path.exists(), f"领域模块不存在: {module}"
    imported = _imported_modules(path)
    assert not any(n == "sqlite3" for n in imported), f"{module} 导入了 sqlite3"


# ======================================================================
# 依赖方向: 领域层 / 应用层 不许依赖 persistence
# ======================================================================

#: 允许直接依赖 ``src.persistence`` 的**顶层包** —— 必须显式列出并写明理由。
#:
#: 为什么要有这张表 (Task 43 的修正)
#: ------------------------------------------------------------------
#: 原来的写法是"除了 ``src/persistence/`` 以外一律禁止"。那在 Task 42 时是
#: 对的 —— 当时确实没有任何上层包依赖 persistence。但**备份层 (Task 43)
#: 必须依赖它**: 一致性快照 (``Database.backup_to``)、``schema_version`` 与
#: 迁移链都只存在于持久化层。在备份层重写这三样, 等于把 WAL 的知识复制一份
#: ——而"直接复制数据库文件会静默备份出一份丢数据的库"正是持久化层文档里
#: 明确警告的那类 bug。
#:
#: 改成"枚举 + 全等断言"之后守卫**更强**了: 新增任何消费者都必须显式改这
#: 张表 (并写理由), 不可能靠"顺手 import 一下"悄悄通过。
ALLOWED_PERSISTENCE_CONSUMERS: dict[str, str] = {
    "backup": (
        "备份 / 恢复层 (Task 43): 需要 Database.backup_to 的一致性快照、"
        "schema_version 与迁移链, 才能实现规范的 'old schema -> current schema'。"
    ),
    "application": (
        "装配层 (Task 44): src/application/bootstrap.py 是组合根, 需要用 "
        "open_database 按 AppConfig.database_path 打开并迁移数据库。"
        "该包内**只有 bootstrap.py 一个文件**允许这么做, 由 "
        "APPLICATION_PERSISTENCE_ENTRYPOINTS 逐文件钉住。"
    ),
}

#: 应用层里允许 import ``src.persistence`` 的**具体文件** (不是整个包)。
#:
#: Task 42 的守卫文档里已经预告了这件事: "若将来确实需要 (例如装配层要开库),
#: 应该放在 Task 44 的 AppConfig 装配代码里, 而不是散在业务服务中 —— 那时把
#: 那个文件加进白名单并说明理由。" 这里就是那张白名单, 而且粒度是**文件**,
#: 比"包"更严: 任何新的业务服务都不可能顺手 import 一下存储层。
APPLICATION_PERSISTENCE_ENTRYPOINTS: dict[str, str] = {
    "bootstrap.py": (
        "组合根: 按配置打开 / 迁移 SQLite 库, 并把 BackupService 绑到同一个 "
        "data_dir。业务服务不得绕过它直接碰存储。"
    ),
    "persistence_wiring.py": (
        "业务对象 <-> SQLite 的接线层 (Task 48-55)。Task 42 建好了仓储, 但"
        "业务对象从来没有被写进那些表 —— 于是'创建课程 -> 内存 -> 关闭程序 "
        "-> 数据消失'。这条接线必须有人负责, 而它不属于任何一个业务服务: "
        "把它收在**一个**文件里, 让'谁依赖存储层'一眼可见, 并且新增业务服务 "
        "仍然一个都不许碰存储 (见 test_application_services_do_not_reach_"
        "into_the_storage_layer)。"
    ),
}


def _importers_of_persistence() -> list[pathlib.Path]:
    """所有直接 import ``src.persistence`` 的模块 (不含 persistence 自身)。"""
    return [
        path
        for path in _python_files(SRC)
        if PERSISTENCE not in path.parents
        and any(
            name == "src.persistence" or name.startswith("src.persistence.")
            for name in _imported_modules(path)
        )
    ]


def test_no_domain_module_imports_the_persistence_package():
    """领域层 (``src/*.py`` 这些模块) 一个文件都不许认识 persistence。

    依赖方向只能是 ``persistence -> domain``。反过来就是分层塌了: 领域对象
    会开始知道"自己被存到哪里", 于是"领域层不认识数据库"这条设计就不再是
    代码里的事实, 而只是一句口号。
    """
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in _importers_of_persistence()
        if len(path.relative_to(SRC).parts) == 1
    ]
    assert offenders == [], f"领域模块不许依赖 persistence: {offenders}"


def test_only_the_documented_packages_depend_on_the_persistence_package():
    """依赖 persistence 的**顶层包**必须与白名单**完全一致**。

    正向与反向断言合一: 多一个包会失败 (新增消费者必须显式登记), 少一个包
    也会失败 (白名单不许变成过期文档)。因此这条既不会空跑, 也没法被绕过。
    """
    packages = {
        path.relative_to(SRC).parts[0]
        for path in _importers_of_persistence()
        if len(path.relative_to(SRC).parts) > 1
    }
    assert packages == set(ALLOWED_PERSISTENCE_CONSUMERS), (
        "依赖 persistence 的包发生了变化 —— 请显式更新 ALLOWED_PERSISTENCE_CONSUMERS "
        f"并写明理由。实际: {sorted(packages)}; 白名单: {sorted(ALLOWED_PERSISTENCE_CONSUMERS)}"
    )


def test_every_allowed_persistence_consumer_still_exists():
    """白名单里的包必须真实存在 —— 否则它就成了指向已删除代码的过期许可。"""
    for package in ALLOWED_PERSISTENCE_CONSUMERS:
        assert (SRC / package).is_dir(), f"白名单里的包不存在: src/{package}"
        assert ALLOWED_PERSISTENCE_CONSUMERS[package].strip(), f"{package} 没有写明理由"


def test_the_application_layer_does_not_import_the_persistence_package():
    """应用层通过仓储**接口**工作, 只有组合根例外。

    Task 44 的修正: 原来这条是"应用层一个文件都不许 import persistence"。
    Task 44 必须开库 (AppConfig.database_path 要有意义), 所以按 Task 42 文档
    里的预告把装配文件加进白名单。粒度取**文件**而不是**包**: 新增业务服务
    仍然一个都不许碰存储层, 而白名单本身用"枚举 + 全等断言"防止过期。
    """
    app = SRC / "application"
    offenders = sorted(
        path.name
        for path in _python_files(app)
        if any(
            n == "src.persistence" or n.startswith("src.persistence.")
            for n in _imported_modules(path)
        )
    )
    assert offenders == sorted(APPLICATION_PERSISTENCE_ENTRYPOINTS), (
        "应用层里依赖 persistence 的文件发生了变化 —— 请显式更新 "
        "APPLICATION_PERSISTENCE_ENTRYPOINTS 并写明理由。"
        f"实际: {offenders}; 白名单: {sorted(APPLICATION_PERSISTENCE_ENTRYPOINTS)}"
    )


def test_every_application_persistence_entrypoint_still_exists():
    """白名单里的文件必须真实存在且写明了理由, 否则它就成了过期许可。"""
    app = SRC / "application"
    for name, reason in APPLICATION_PERSISTENCE_ENTRYPOINTS.items():
        assert (app / name).is_file(), f"白名单里的文件不存在: src/application/{name}"
        assert reason.strip(), f"{name} 没有写明理由"


def test_application_services_do_not_reach_into_the_storage_layer():
    """除了组合根, 应用层的每一个模块都必须与存储层无关。"""
    app = SRC / "application"
    allowed = set(APPLICATION_PERSISTENCE_ENTRYPOINTS)
    offenders = sorted(
        path.name
        for path in _python_files(app)
        if path.name not in allowed
        and any(
            n == "src.persistence" or n.startswith("src.persistence.")
            for n in _imported_modules(path)
        )
    )
    assert offenders == [], f"业务服务不应依赖 persistence: {offenders}"


def test_the_web_layer_does_not_import_the_persistence_package():
    """UI / HTTP 层绝不能绕过 Domain 直接摸数据库。"""
    web = SRC / "web"
    if not web.exists():
        pytest.skip("src/web 不存在")
    offenders = [
        path.relative_to(SRC).as_posix()
        for path in _python_files(web)
        if any(
            n == "src.persistence" or n.startswith("src.persistence.")
            for n in _imported_modules(path)
        )
    ]
    assert offenders == [], f"web 层不应依赖 persistence: {offenders}"


# ======================================================================
# P1-4 双向守卫: persistence 不得反向依赖上层包
# ======================================================================


#: ``src.persistence`` 不得依赖的上层包 (方向必须单向: 上层 -> persistence)。
#: 允许依赖领域模型 (src.models / src.knowledge_structure / ...), 但**绝不**
#: 反向 import ``src.application`` / ``src.backup`` / ``src.api`` / ``src.web``
#: —— 否则就形成了"持久化层认识装配层 / 备份层"的逆向环。
FORBIDDEN_PERSISTENCE_DEPENDENCIES: tuple[str, ...] = (
    "src.application",
    "src.backup",
    "src.api",
    "src.web",
)


def test_persistence_does_not_import_upper_layer_packages():
    """反向断言 (P1-4): ``src/persistence`` 永远不许反向依赖上层包。

    原守卫只查"谁引 persistence"; 但 ``src/persistence/database.py`` 曾经在
    模块导入期 ``from src.application.runtime import Clock`` —— 这是一条
    静默的逆向环。把 Clock 收口到零依赖的 ``src/persistence/clock`` 之后, 这条守卫
    必须钉死: 任何上层包都不许再被 persistence 引入。
    """
    offenders: list[str] = []
    for path in _python_files(PERSISTENCE):
        for name in _imported_modules(path):
            if any(
                name == f or name.startswith(f + ".")
                for f in FORBIDDEN_PERSISTENCE_DEPENDENCIES
            ):
                offenders.append(f"{path.relative_to(SRC).as_posix()} -> {name}")
    assert offenders == [], (
        "src/persistence 不得反向依赖上层包: " + "; ".join(offenders)
    )


def test_models_module_does_not_import_upper_layer_packages():
    """``src/models.py`` 是领域核心, 不得认识任何上层模块 (P1-4)。

    历史上 ``KnowledgePoint.__post_init__`` 在运行时懒导入
    ``src.knowledge_validation`` / ``src.knowledge_review`` 来取
    ``ValidationStatus`` / ``ReviewStatus`` —— 这是领域核心对上层的隐式依赖。
    两个枚举已收口到 ``src.models`` 自身, 此断言钉死: models 不再 import 任何
    ``src.knowledge_*`` 或 ``src.application``。
    """
    path = SRC / "models.py"
    assert path.exists(), "领域核心模块不存在: src/models.py"
    imported = _imported_modules(path)
    offenders = sorted(
        n
        for n in imported
        if n.startswith("src.knowledge_") or n == "src.application"
        or n.startswith("src.application.")
    )
    assert offenders == [], f"src/models.py 不应依赖上层模块: {offenders}"


# ======================================================================
# 包结构符合规范
# ======================================================================


@pytest.mark.parametrize(
    "relative",
    ["database.py", "repositories", "migrations", "models"],
)
def test_required_persistence_submodules_exist(relative):
    """规范点名要求的结构: database.py / repositories / migrations / models。"""
    assert (PERSISTENCE / relative).exists(), f"缺少 src/persistence/{relative}"


def test_every_repository_module_declares_a_repository_class():
    """每个仓储模块至少导出一个 ``*Repository``, 避免出现空壳文件。"""
    modules = [
        p for p in _python_files(PERSISTENCE / "repositories") if p.name != "__init__.py"
    ]
    assert modules, "repositories/ 下没有任何模块"
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        classes = [
            node.name
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name.endswith("Repository")
        ]
        assert classes, f"{path.name} 里没有定义任何 *Repository 类"


def test_persistence_does_not_import_an_external_database_driver():
    """规范: 零独立数据库服务 —— 不许出现 postgres / mysql / redis 之类。"""
    banned = ("psycopg2", "psycopg", "pymysql", "MySQLdb", "redis", "pymongo", "sqlalchemy")
    offenders: list[str] = []
    for path in _python_files(PERSISTENCE):
        for name in _imported_modules(path):
            if any(name == b or name.startswith(b + ".") for b in banned):
                offenders.append(f"{path.name} -> {name}")
    assert offenders == [], f"持久化层不应依赖外部数据库驱动: {offenders}"
