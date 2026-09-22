# -*- coding: utf-8 -*-
"""Task 43 —— 恢复的原子性 / 失败回滚 / 版本迁移测试。

规范原文::

    ## Recovery
    如果 restore 失败: 原数据库不能被破坏。
    采用: validate -> restore to temporary location -> verify -> atomic replace

    ## Migration
    建立版本迁移机制。
    测试: old schema -> current schema

本文件的核心不是"恢复能成功", 而是**"恢复失败时原数据一定没被破坏"**。
所以每一条失败路径都用**真实异常注入**验证, 并断言:

- 活库的字节摘要与失败前**逐字节相同**;
- 材料树里每个文件的摘要都没变;
- 不留任何 ``temp/restore-*`` 工作目录。

为什么用字节摘要而不是"数据看起来还在"
--------------------------------------------------------------------
"看起来还在"只能证明我记得的那几个字段没丢。摘要相同证明**整份文件**
没被动过 —— 而恢复路径上最危险的失败模式恰恰是"文件被替换成了一半"。
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import shutil

import pytest

from src.application.data_dirs import remove_quietly
from src.application.runtime import fixed_clock
from src.backup.errors import (
    BackupNotFoundError,
    BackupValidationError,
    ChecksumMismatchError,
    DatabaseInUseError,
    RestoreError,
    UnsupportedBackupVersionError,
)
from src.backup.service import (
    BACKED_UP_DIRS,
    RestoreResult,
    BackupService,
    _translate_replace_error,
    restore_backup,
)
from src.backup.manifest import DATABASE_ENTRY_NAME
from src.persistence.database import Database
from src.persistence.errors import CorruptedDatabaseError
from src.persistence.migrations import latest_version
from src.persistence.migrations.m001_initial_schema import MIGRATION_001
from tests.test_backup_service import (
    DEFAULT_MATERIALS,
    _archive_course_names,
    _archive_entry,
    _fixture,
    _live_course_names,
    _rewrite_archive,
    _seed_database,
    _seed_materials,
    _service,
    _tree_files,
    _write_pairs,
)

#: ``BackupService`` 里恢复工作目录的前缀 (私有常量, 这里按契约断言)。
RESTORE_WORK_PREFIX = "restore-"

#: 恢复成功后 ``replaced_dirs`` 的固定顺序。
EXPECTED_REPLACED = ("database", *BACKED_UP_DIRS)

#: ``_seed_database`` 写入的两门课, 按 ``sorted()`` 的**码点顺序**排列。
#:
#: 注意 "Càlcul" 排在 "Álgebra" 前面 (``C`` = 0x43 < ``Á`` = 0xC1) ——
#: 这不是笔误, 是 Python 字符串排序的既定行为。
COURSE_NAMES = sorted(["Álgebra", "Càlcul"])


# ======================================================================
# 构造辅助
# ======================================================================


def _digest(path: str) -> str:
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def _wipe(service: BackupService) -> None:
    """清空 data_dir 里的用户数据 (模拟换机器 / 误删 / 灾难恢复)。"""
    for suffix in ("", "-wal", "-shm"):
        remove_quietly(service.database_path + suffix)
    for tree in BACKED_UP_DIRS:
        directory = getattr(service.layout, tree)
        remove_quietly(directory)
        os.makedirs(directory, exist_ok=True)


def _live_schema_version(service: BackupService) -> int:
    database = Database(service.database_path)
    try:
        return database.schema_version()
    finally:
        database.close()


def _old_schema_service(tmp_path) -> BackupService:
    """一个只有 migration 001 的库 (即"老版本应用留下的数据")。"""
    service = _service(str(tmp_path / "data"))
    database = Database(service.database_path)
    try:
        database.migrate(migrations=(MIGRATION_001,))
    finally:
        database.close()
    assert _live_schema_version(service) == 1
    return service


def _future_schema_database(path: str) -> str:
    """造一个"来自未来"的库: 台账里写着版本 99。"""
    database = Database(path)
    try:
        database.migrate()
        with database.transaction():
            database.execute("INSERT INTO schema_version (version, name) VALUES (99, 'future')")
    finally:
        database.close()
    return path


def _work_dirs(service: BackupService) -> list[str]:
    return sorted(
        name for name in os.listdir(service.layout.temp) if name.startswith(RESTORE_WORK_PREFIX)
    )


def _material_files(service: BackupService) -> dict[str, str]:
    """四棵材料树的逐文件摘要 (相对 ``data_dir``)。

    刻意**不含** ``database/``: 活库文件与归档里的快照内容相同但字节不同
    (SQLite 的页分配与文件头计数不同), 所以"活库文件摘要"不能当作
    "数据没变"的判据。数据库要用**内容**断言 (见 ``_live_course_names``),
    材料才用字节断言。
    """
    out: dict[str, str] = {}
    for tree in BACKED_UP_DIRS:
        for relative, digest in _tree_files(getattr(service.layout, tree)).items():
            out[f"{tree}/{relative}"] = digest
    return out


def _three_tree_service(tmp_path, name: str = "data") -> BackupService:
    """只备份 materials / audio / documents 三棵树的服务 (用于"未声明的树"测试)。"""
    service = _service(str(tmp_path / name), backed_up_dirs=("materials", "audio", "documents"))
    _seed_database(service)
    _seed_materials(
        service,
        {key: value for key, value in DEFAULT_MATERIALS.items() if not key.startswith("images/")},
    )
    return service


# ======================================================================
# 正常往返
# ======================================================================


def test_restore_brings_back_the_database_and_the_materials(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    _wipe(service)
    # 清空之后库里**没有** ``courses`` 表 —— 所以这里断言"文件不存在",
    # 而不是去查一个空库 (那只会得到 "no such table")。
    assert not os.path.exists(service.database_path)
    assert os.listdir(service.layout.audio) == []
    restored = service.restore_backup(result.archive_path)
    assert isinstance(restored, RestoreResult)
    assert restored.archive_path == result.archive_path
    assert restored.data_dir == service.data_dir
    assert restored.database_path == service.database_path
    assert restored.replaced_dirs == EXPECTED_REPLACED
    assert restored.material_file_count == len(DEFAULT_MATERIALS)
    assert restored.warnings == ()
    assert _live_course_names(service) == COURSE_NAMES


def test_restore_keeps_the_exact_bytes_of_every_material(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    _wipe(service)
    service.restore_backup(result.archive_path)
    for relative, content in DEFAULT_MATERIALS.items():
        target = os.path.join(service.data_dir, *relative.split("/"))
        assert pathlib.Path(target).read_bytes() == content, relative


def test_restore_keeps_multilingual_names_and_content(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    _wipe(service)
    service.restore_backup(result.archive_path)
    restored = pathlib.Path(service.layout.documents, "apuntes-álgebra.txt")
    assert restored.read_text(encoding="utf-8") == "函数是一种关系。"


def test_restore_puts_the_archived_database_bytes_in_place(tmp_path):
    """恢复出来的活库就是归档里那份数据库 —— 逐字节相同。"""
    service = _fixture(tmp_path)
    result = service.create_backup()
    expected = hashlib.sha256(_archive_entry(result.archive_path, DATABASE_ENTRY_NAME)).hexdigest()
    _seed_database(service, courses=[("course-3", "Física", "FIS")])
    assert "Física" in _live_course_names(service)
    service.restore_backup(result.archive_path)
    assert _live_course_names(service) == COURSE_NAMES
    assert _digest(service.database_path) == expected


def test_restore_into_a_fresh_data_dir_recreates_everything(tmp_path):
    """灾难恢复: 换一台机器 / 换一个 data_dir, 也能完整还原。"""
    source = _fixture(tmp_path)
    result = source.create_backup()
    target = _service(str(tmp_path / "recovered"))
    restored = target.restore_backup(result.archive_path)
    assert restored.replaced_dirs == EXPECTED_REPLACED
    assert _live_course_names(target) == _live_course_names(source)
    for relative, content in DEFAULT_MATERIALS.items():
        path = os.path.join(target.data_dir, *relative.split("/"))
        assert pathlib.Path(path).read_bytes() == content


def test_restore_from_an_archive_copied_elsewhere(tmp_path):
    """归档是可以被搬运的 —— 从别处拷来的备份一样能恢复。"""
    source = _fixture(tmp_path)
    result = source.create_backup()
    copy = tmp_path / "usb-stick" / "backup.zip"
    copy.parent.mkdir()
    copy.write_bytes(pathlib.Path(result.archive_path).read_bytes())
    target = _service(str(tmp_path / "recovered"))
    target.restore_backup(str(copy))
    assert _live_course_names(target) == COURSE_NAMES


def test_restore_handles_a_deeply_nested_material_path(tmp_path):
    service = _service(str(tmp_path / "data"))
    _seed_database(service)
    _seed_materials(service, {"documents/2026/tema-1/sub/apuntes.txt": b"deep"})
    result = service.create_backup()
    _wipe(service)
    service.restore_backup(result.archive_path)
    restored = pathlib.Path(
        service.layout.documents, "2026", "tema-1", "sub", "apuntes.txt"
    )
    assert restored.read_bytes() == b"deep"


def test_restore_recreates_a_material_tree_that_was_deleted(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    shutil.rmtree(service.layout.documents)
    service.restore_backup(result.archive_path)
    assert pathlib.Path(service.layout.documents, "apuntes-álgebra.txt").exists()


def test_restore_is_idempotent(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    service.restore_backup(result.archive_path)
    after_first = _tree_files(service.data_dir)
    service.restore_backup(result.archive_path)
    assert _tree_files(service.data_dir) == after_first


def test_restore_then_backup_reproduces_the_same_archive(tmp_path):
    """恢复后再备份一次, 得到的归档与原来**逐字节相同**。

    这是"备份 -> 恢复"这一对操作互为逆操作的最强证据: 数据、清单、条目
    时间戳全部可复现。
    """
    service = _fixture(tmp_path)
    first = service.create_backup()
    digest = _digest(first.archive_path)
    service.restore_backup(first.archive_path)
    again = service.create_backup(overwrite=True)
    assert again.archive_path == first.archive_path
    assert _digest(again.archive_path) == digest


def test_restored_data_survives_repeated_reopens(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    _wipe(service)
    service.restore_backup(result.archive_path)
    for _ in range(3):
        assert _live_course_names(service) == COURSE_NAMES


def test_restore_leaves_no_work_directory(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    service.restore_backup(result.archive_path)
    assert _work_dirs(service) == []


def test_restore_can_go_back_and_forth_between_two_backups(tmp_path):
    """"回滚到昨天那版"是备份最实用的用法之一。"""
    service = _fixture(tmp_path)
    older = service.create_backup()
    _seed_database(service, courses=[("course-3", "Física", "FIS")])
    newer = _service(
        service.data_dir, clock=fixed_clock("2026-09-16T09:00:00+00:00")
    ).create_backup()

    service.restore_backup(older.archive_path)
    assert _live_course_names(service) == COURSE_NAMES
    service.restore_backup(newer.archive_path)
    assert "Física" in _live_course_names(service)


# ======================================================================
# 恢复策略: 哪些树该替换
# ======================================================================


def test_restore_without_materials_only_replaces_the_database(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    _seed_materials(service, {"audio/new.mp3": b"new audio"})
    restored = service.restore_backup(result.archive_path, restore_materials=False)
    assert restored.replaced_dirs == ("database",)
    assert pathlib.Path(service.layout.audio, "new.mp3").read_bytes() == b"new audio"


def test_restore_empties_a_declared_tree_that_had_no_files(tmp_path):
    """归档**声明**了自己的目录构成 -> 忠实还原 (空树就该是空的)。

    否则"恢复"就变成了"恢复 + 保留后来的新增", 结果不可预期。
    """
    source = _service(str(tmp_path / "source"))
    _seed_database(source)
    _seed_materials(
        source,
        {key: value for key, value in DEFAULT_MATERIALS.items() if not key.startswith("images/")},
    )
    result = source.create_backup()

    target = _service(str(tmp_path / "target"))
    _seed_materials(target, {"images/board-01.png": b"old"})
    restored = target.restore_backup(result.archive_path)
    assert "images" in restored.replaced_dirs
    assert restored.warnings == ()
    assert os.listdir(target.layout.images) == []


def test_restore_leaves_undeclared_trees_untouched(tmp_path):
    """归档**没**声明目录构成 (老归档 / 手工归档) -> 信息不足时不破坏。"""
    source = _three_tree_service(tmp_path, "source")
    result = source.create_backup()
    undeclared = _rewrite_archive(
        result.archive_path,
        str(tmp_path / "undeclared.zip"),
        manifest_edit=lambda manifest: manifest["config"].pop("backed_up_dirs", None),
    )

    target = _service(str(tmp_path / "target"))
    _seed_materials(target, {"images/new-board.png": b"new"})
    restored = target.restore_backup(undeclared)
    assert "images" not in restored.replaced_dirs
    assert any("images/" in warning for warning in restored.warnings)
    assert pathlib.Path(target.layout.images, "new-board.png").read_bytes() == b"new"


def test_restore_of_an_undeclared_archive_still_replaces_the_trees_it_carries(tmp_path):
    source = _three_tree_service(tmp_path, "source")
    result = source.create_backup()
    undeclared = _rewrite_archive(
        result.archive_path,
        str(tmp_path / "undeclared.zip"),
        manifest_edit=lambda manifest: manifest["config"].pop("backed_up_dirs", None),
    )
    target = _service(str(tmp_path / "target"))
    restored = target.restore_backup(undeclared)
    assert set(restored.replaced_dirs) == {"database", "materials", "audio", "documents"}
    assert _live_course_names(target) == COURSE_NAMES


# ======================================================================
# 校验失败: 一个字节都不许动
# ======================================================================


def test_restore_refuses_an_invalid_archive(tmp_path):
    service = _fixture(tmp_path)
    bad = _write_pairs(str(tmp_path / "bad.zip"), [("hello.txt", b"hi")])
    before = _tree_files(service.data_dir)
    with pytest.raises(RestoreError) as caught:
        service.restore_backup(bad)
    assert "failed validation" in str(caught.value)
    assert caught.value.detail["errors"]
    assert _tree_files(service.data_dir) == before
    assert _work_dirs(service) == []


def test_restore_refuses_a_corrupt_archive(tmp_path):
    service = _fixture(tmp_path)
    bad = tmp_path / "corrupt.zip"
    bad.write_bytes(b"definitely not a zip")
    before = _tree_files(service.data_dir)
    with pytest.raises(RestoreError):
        service.restore_backup(str(bad))
    assert _tree_files(service.data_dir) == before


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_restore_rejects_a_blank_path(tmp_path, bad):
    with pytest.raises(BackupValidationError):
        _fixture(tmp_path).restore_backup(bad)


def test_restore_raises_not_found_for_a_missing_archive(tmp_path):
    with pytest.raises(BackupNotFoundError) as caught:
        _fixture(tmp_path).restore_backup(str(tmp_path / "nope.zip"))
    assert caught.value.code == "BACKUP_NOT_FOUND"


# ======================================================================
# 失败注入: 原数据库不能被破坏
# ======================================================================


def test_restore_failure_before_the_swap_keeps_the_original_data(tmp_path, monkeypatch):
    """在第一个破坏性步骤**之前**失败 -> 什么都没改。"""
    service = _fixture(tmp_path)
    result = service.create_backup()
    _seed_database(service, courses=[("course-3", "Física", "FIS")])
    before_digest = _digest(service.database_path)
    before_files = _tree_files(service.data_dir)

    def boom(self, staged_db):
        raise RuntimeError("simulated integrity probe failure")

    monkeypatch.setattr(BackupService, "_verify_staged_database", boom)
    with pytest.raises(RestoreError) as caught:
        service.restore_backup(result.archive_path)
    assert "rolled back" in str(caught.value)
    assert isinstance(caught.value.cause, RuntimeError)
    assert _digest(service.database_path) == before_digest
    assert _tree_files(service.data_dir) == before_files
    assert _work_dirs(service) == []


def test_restore_failure_after_the_database_swap_rolls_back(tmp_path, monkeypatch):
    """数据库已经被换掉了, 材料替换失败 -> **必须把数据库挪回来**。"""
    service = _fixture(tmp_path)
    result = service.create_backup()
    _seed_database(service, courses=[("course-3", "Física", "FIS")])
    before_digest = _digest(service.database_path)
    before_files = _tree_files(service.data_dir)

    def boom(self, work, manifest, previous, entries, warnings):
        raise RuntimeError("simulated material replacement failure")

    monkeypatch.setattr(BackupService, "_replace_materials", boom)
    with pytest.raises(RestoreError):
        service.restore_backup(result.archive_path)

    assert _digest(service.database_path) == before_digest
    assert _tree_files(service.data_dir) == before_files
    assert "Física" in _live_course_names(service)
    assert _work_dirs(service) == []


def test_restore_failure_after_the_swap_rolls_back_every_material_tree(tmp_path, monkeypatch):
    """回滚必须把**每一棵**已经挪走的树挪回来, 不只是数据库。

    这里注入的是**真实的 Windows 失败模式**: 目录里有文件被占用时,
    ``os.rename`` 报 ``WinError 5 (拒绝访问)``。材料树按
    ``materials -> audio -> images -> documents`` 顺序处理, 所以让
    ``audio`` 那一步失败, 就能精确制造"第一棵树已经挪走、第二棵树失败"
    这个中间状态 —— 而这正是回滚代码最容易被写错的地方。
    """
    service = _fixture(tmp_path)
    result = service.create_backup()
    _seed_materials(service, {"audio/later-recording.mp3": b"later"})
    before_digest = _digest(service.database_path)
    before_files = _tree_files(service.data_dir)

    real_rename = os.rename
    blocked = os.path.join(service.layout.audio)

    def flaky(src, dst):
        if os.path.abspath(src) == blocked:
            raise PermissionError(13, "Access is denied")
        return real_rename(src, dst)

    monkeypatch.setattr(os, "rename", flaky)
    with pytest.raises(DatabaseInUseError) as caught:
        service.restore_backup(result.archive_path)
    monkeypatch.undo()

    assert caught.value.detail["path"] == blocked
    assert _digest(service.database_path) == before_digest
    assert _tree_files(service.data_dir) == before_files
    assert pathlib.Path(service.layout.audio, "later-recording.mp3").read_bytes() == b"later"
    assert _work_dirs(service) == []


def test_restore_does_not_double_wrap_a_backup_error(tmp_path, monkeypatch):
    """已经是结构化备份错误 -> 原样抛出, 不再包一层泛泛的 RestoreError。

    包一层会把精确的错误码 (``STORAGE_BACKUP_CHECKSUM_MISMATCH``) 降级成
    ``STORAGE_RESTORE_FAILED``, 用户就看不到真正的原因了。
    """
    service = _fixture(tmp_path)
    result = service.create_backup()

    def boom(self, staged_db):
        raise ChecksumMismatchError("staged database changed under our feet")

    monkeypatch.setattr(BackupService, "_verify_staged_database", boom)
    with pytest.raises(ChecksumMismatchError) as caught:
        service.restore_backup(result.archive_path)
    assert caught.value.code == "STORAGE_BACKUP_CHECKSUM_MISMATCH"
    assert "rolled back" not in str(caught.value)


def test_restore_wraps_an_unexpected_failure_as_a_restore_error(tmp_path, monkeypatch):
    """非备份层的异常 -> 统一包成 RestoreError, 并保留 cause 供开发日志用。"""
    service = _fixture(tmp_path)
    result = service.create_backup()

    def boom(self, work, manifest, previous, entries, warnings):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(BackupService, "_replace_materials", boom)
    with pytest.raises(RestoreError) as caught:
        service.restore_backup(result.archive_path)
    assert caught.value.code == "STORAGE_RESTORE_FAILED"
    assert isinstance(caught.value.cause, OSError)


# ======================================================================
# Windows 文件占用: 恢复是离线操作
# ======================================================================


def test_restore_raises_database_in_use_when_the_live_database_is_open(tmp_path):
    """只要还有句柄持有活库, Windows 上的 ``os.replace`` 就会失败。

    这时必须给出**可执行**的提示, 而不是裸 ``PermissionError``。
    """
    service = _fixture(tmp_path)
    result = service.create_backup()
    before_digest = _digest(service.database_path)

    held = Database(service.database_path)
    held.connect()
    try:
        with pytest.raises(DatabaseInUseError) as caught:
            service.restore_backup(result.archive_path)
        assert caught.value.code == "STORAGE_DATABASE_IN_USE"
        assert "Close the application" in str(caught.value)
        assert caught.value.detail["path"] == service.database_path
    finally:
        held.close()

    assert _digest(service.database_path) == before_digest
    assert _work_dirs(service) == []


def test_restore_succeeds_once_the_connection_is_closed(tmp_path):
    """同一个归档, 关掉句柄之后就能恢复 —— 证明失败原因确实是占用。"""
    service = _fixture(tmp_path)
    result = service.create_backup()
    held = Database(service.database_path)
    held.connect()
    held.close()
    assert service.restore_backup(result.archive_path).replaced_dirs == EXPECTED_REPLACED


def test_translate_replace_error_maps_a_permission_error_to_database_in_use():
    error = _translate_replace_error(PermissionError(13, "Access is denied"), "C:/x.sqlite")
    assert isinstance(error, DatabaseInUseError)
    assert error.code == "STORAGE_DATABASE_IN_USE"
    assert error.detail["path"] == "C:/x.sqlite"


def test_translate_replace_error_maps_winerror_32_to_database_in_use():
    """WinError 32 = "正被另一个进程使用"。"""
    exc = OSError(13, "in use", "x.sqlite", 32)
    assert exc.winerror == 32  # 前提: 这个构造形式真的设上了 winerror
    assert isinstance(_translate_replace_error(exc, "x.sqlite"), DatabaseInUseError)


def test_translate_replace_error_maps_other_errors_to_restore_error():
    """磁盘满了不是"文件被占用" —— 提示不能指错方向。"""
    error = _translate_replace_error(OSError(28, "No space left on device"), "x.sqlite")
    assert isinstance(error, RestoreError)
    assert error.code == "STORAGE_RESTORE_FAILED"
    assert "Close the application" not in str(error)


# ======================================================================
# WAL 边车文件
# ======================================================================


def test_restore_clears_stale_wal_sidecar_files(tmp_path):
    """必须先清掉 ``-wal`` / ``-shm``, 否则 SQLite 会把旧 WAL 重放到新库上。

    那是最坏的一类数据损坏: 静默混合两份数据。
    """
    service = _fixture(tmp_path)
    result = service.create_backup()
    for suffix in ("-wal", "-shm"):
        pathlib.Path(service.database_path + suffix).write_bytes(b"stale")
    service.restore_backup(result.archive_path)
    for suffix in ("-wal", "-shm"):
        assert not os.path.exists(service.database_path + suffix)
    assert _live_course_names(service) == COURSE_NAMES


def test_restore_leaves_the_database_usable_after_clearing_sidecars(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    pathlib.Path(service.database_path + "-wal").write_bytes(b"stale")
    service.restore_backup(result.archive_path)
    database = Database(service.database_path)
    try:
        assert database.is_healthy()
    finally:
        database.close()


# ======================================================================
# 版本迁移: old schema -> current schema
# ======================================================================


def test_create_backup_records_the_schema_version_of_an_old_database(tmp_path):
    service = _old_schema_service(tmp_path)
    result = service.create_backup()
    assert result.manifest.schema_version == 1
    assert result.manifest.schema_version < latest_version()


def test_restore_migrates_an_old_schema_archive_to_current(tmp_path):
    service = _old_schema_service(tmp_path)
    # 刻意复用只到 001 的迁移链 —— 否则 ``_seed_database`` 会顺手把库升到最新,
    # 那样测的就不是"迁移老归档"了。
    _seed_database(
        service, migrations=(MIGRATION_001,), courses=[("course-1", "Álgebra", "ALG")]
    )
    assert _live_schema_version(service) == 1
    result = service.create_backup()
    assert result.manifest.schema_version == 1

    restored = service.restore_backup(result.archive_path)
    assert restored.migrated_from == 1
    assert restored.migrated_to == latest_version()
    assert _live_schema_version(service) == latest_version()
    assert _live_course_names(service) == ["Álgebra"]


def test_restore_of_an_old_schema_archive_into_a_fresh_dir(tmp_path):
    """老库 + 空目标目录: 迁移与重建都要成立。"""
    source = _old_schema_service(tmp_path)
    result = source.create_backup()
    target = _service(str(tmp_path / "fresh"))
    restored = target.restore_backup(result.archive_path)
    assert restored.migrated_to == latest_version()
    assert _live_schema_version(target) == latest_version()


def test_restore_with_migrate_false_keeps_the_old_schema(tmp_path):
    """``migrate=False`` 用于"先原样放回去, 迁移以后再说"的场景。"""
    service = _old_schema_service(tmp_path)
    result = service.create_backup()
    restored = service.restore_backup(result.archive_path, migrate=False)
    assert restored.migrated_from is None
    assert restored.migrated_to is None
    assert _live_schema_version(service) == 1


def test_restore_does_not_migrate_a_current_schema_archive(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    restored = service.restore_backup(result.archive_path)
    assert restored.migrated_from is None
    assert restored.migrated_to is None
    assert _live_schema_version(service) == latest_version()


def test_migrate_staged_database_rejects_a_newer_schema(tmp_path):
    service = _service(str(tmp_path / "data"))
    staged = _future_schema_database(str(tmp_path / "future.sqlite"))
    with pytest.raises(UnsupportedBackupVersionError) as caught:
        service._migrate_staged_database(staged)
    assert caught.value.code == "INVALID_BACKUP_VERSION"
    assert "newer than this build supports" in str(caught.value)


def test_verify_staged_database_rejects_a_newer_schema(tmp_path):
    """清单是可以被改的 (它就是个 JSON 文件) —— 真正的判据是库里的台账。"""
    service = _service(str(tmp_path / "data"))
    staged = _future_schema_database(str(tmp_path / "future.sqlite"))
    with pytest.raises(UnsupportedBackupVersionError):
        service._verify_staged_database(staged)


def test_verify_staged_database_rejects_an_unreadable_file(tmp_path):
    service = _service(str(tmp_path / "data"))
    garbage = str(tmp_path / "garbage.sqlite")
    pathlib.Path(garbage).write_bytes(b"not a database at all")
    with pytest.raises(CorruptedDatabaseError) as caught:
        service._verify_staged_database(garbage)
    assert caught.value.code == "STORAGE_CORRUPTED_DATABASE"


def test_restore_rejects_an_archive_from_a_newer_schema(tmp_path):
    """来自更新版本的备份必须在**碰任何现有数据之前**被拒绝。"""
    service = _fixture(tmp_path)
    result = service.create_backup()
    future = _rewrite_archive(
        result.archive_path,
        str(tmp_path / "future.zip"),
        manifest_edit=lambda manifest: manifest.__setitem__(
            "schema_version", latest_version() + 1
        ),
    )
    before = _tree_files(service.data_dir)
    with pytest.raises(UnsupportedBackupVersionError) as caught:
        service.restore_backup(future)
    assert caught.value.code == "INVALID_BACKUP_VERSION"
    assert "refusing to restore" in str(caught.value)
    assert _tree_files(service.data_dir) == before
    assert _work_dirs(service) == []


def test_restore_accepts_an_archive_with_the_current_schema(tmp_path):
    """对照组: 同一个归档, 版本没被改过就能恢复 —— 证明上面拦的是版本。"""
    service = _fixture(tmp_path)
    result = service.create_backup()
    assert service.restore_backup(result.archive_path).migrated_to is None


# ======================================================================
# 模块级便捷函数
# ======================================================================


def test_module_restore_backup_works(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    _wipe(service)
    restored = restore_backup(result.archive_path, service.data_dir)
    assert isinstance(restored, RestoreResult)
    assert _live_course_names(service) == COURSE_NAMES


def test_module_restore_backup_accepts_operation_arguments(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    _seed_materials(service, {"audio/new.mp3": b"new"})
    restored = restore_backup(result.archive_path, service.data_dir, restore_materials=False)
    assert restored.replaced_dirs == ("database",)
    assert pathlib.Path(service.layout.audio, "new.mp3").exists()


def test_module_restore_backup_can_restore_into_a_new_data_dir(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    elsewhere = str(tmp_path / "elsewhere")
    restored = restore_backup(result.archive_path, elsewhere)
    assert restored.data_dir == os.path.abspath(elsewhere)
    assert os.path.isfile(os.path.join(elsewhere, "database", "classroom.sqlite"))


# ======================================================================
# 端到端: 备份 -> 改数据 -> 恢复 -> 再备份
# ======================================================================


def test_full_backup_restore_cycle_is_lossless(tmp_path):
    """一整轮下来, 材料树的**逐文件摘要**必须完全回到备份时的样子。"""
    service = _fixture(tmp_path)
    result = service.create_backup()
    baseline = _material_files(service)

    _seed_database(service, courses=[("course-3", "Física", "FIS")])
    _seed_materials(service, {"audio/extra.mp3": b"extra", "images/extra.png": b"extra"})
    shutil.rmtree(service.layout.documents)
    assert _material_files(service) != baseline

    service.restore_backup(result.archive_path)
    assert _material_files(service) == baseline
    assert _archive_course_names(result.archive_path, tmp_path) == _live_course_names(service)
