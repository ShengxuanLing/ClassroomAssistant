# -*- coding: utf-8 -*-
"""Task 43 —— 备份服务 (create / list / validate / delete / prune) 测试。

规范点名的四个操作::

    create_backup() / list_backups() / restore_backup() / validate_backup()

``restore_backup`` 的原子性与失败回滚在 ``test_backup_recovery.py``;
本文件覆盖其余部分, 外加两件容易被忽略的事:

- **归档里到底装了什么**: 清单字段、校验和、条目名、条目时间戳;
- **什么不该进归档**: ``logs/`` ``temp/`` ``backups/`` ``__pycache__``。

为什么"校验"要单独测这么细
--------------------------------------------------------------------
``validate_archive`` 的契约是"**报告**, 不抛异常"。这条契约的价值在于:
调用方 (UI / API) 需要把"这份备份哪里有问题"逐条展示给用户, 而不是
收到一个异常。所以本文件对**每一类**损坏都构造了一份真实归档, 断言
``ok is False`` 且 ``errors`` 里能读到原因 —— 不是断言"会抛错"。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import pathlib
import stat
import warnings
import zipfile

import pytest

from src.application.runtime import fixed_clock
from src.backup.errors import (
    BackupNotFoundError,
    BackupValidationError,
    DuplicateBackupError,
)
from src.backup.manifest import (
    BACKUP_VERSION,
    DATABASE_ENTRY_NAME,
    MANIFEST_ENTRY_NAME,
)
from src.backup.service import (
    BACKED_UP_DIRS,
    BACKUP_FILE_PREFIX,
    DEFAULT_DATABASE_FILENAME,
    EXCLUDED_DIRS,
    BackupInfo,
    BackupResult,
    BackupService,
    BackupValidation,
    RestoreResult,
    create_backup,
    list_backups,
    validate_archive,
    validate_backup,
)
from src.persistence.database import Database
from src.persistence.migrations import latest_version
from src.persistence.repositories import Repositories
from tests.test_persistence_repositories import _course

#: 固定时钟 —— 归档名与清单里的 ``created_at`` 都来自它, 因此测试可以断言
#: 具体文件名与具体字节。
FIXED_TIME = "2026-09-15T18:00:00+00:00"

#: 固定时钟对应的归档名。
FIXED_NAME = "backup-20260915T180000Z.zip"

#: 默认材料: 四棵树各一个, 含西语/加泰语/中文与二进制内容。
DEFAULT_MATERIALS: dict[str, bytes] = {
    "materials/course-1.json": '{"course_id": "course-1"}'.encode("utf-8"),
    "audio/clase1.mp3": bytes(range(256)) * 16,
    "documents/apuntes-álgebra.txt": "函数是一种关系。".encode("utf-8"),
    "images/board-01.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 64,
}


# ======================================================================
# 构造辅助 (被 test_backup_recovery.py 复用)
# ======================================================================


def _service(data_dir: str, **overrides) -> BackupService:
    """一个用固定时钟构造的服务 (参数可覆盖)。"""
    options = {"clock": fixed_clock(FIXED_TIME), "application_version": "1.0.0"}
    options.update(overrides)
    return BackupService(data_dir, **options)


def _seed_database(service: BackupService, *, migrations=None, courses=None) -> None:
    """在活库位置建一个已迁移的库并写入课程。"""
    rows = courses if courses is not None else (
        ("course-1", "Álgebra", "ALG"),
        ("course-2", "Càlcul", "CAL"),
    )
    database = Database(service.database_path)
    try:
        database.migrate(migrations=migrations)
        repos = Repositories(database)
        for course_id, name, code in rows:
            repos.courses.save(_course(course_id=course_id, name=name, code=code))
    finally:
        database.close()


def _seed_materials(service: BackupService, files: dict[str, bytes] | None = None) -> dict[str, bytes]:
    """往 data_dir 的四棵树里放文件 (相对路径 -> 内容)。"""
    payload = dict(DEFAULT_MATERIALS if files is None else files)
    for relative, content in payload.items():
        target = os.path.join(service.data_dir, *relative.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        pathlib.Path(target).write_bytes(content)
    return payload


def _live_course_names(service: BackupService) -> list[str]:
    database = Database(service.database_path)
    try:
        return sorted(course.name for course in Repositories(database).courses.load_all())
    finally:
        database.close()


def _archive_pairs(archive_path: str) -> list[tuple[str, bytes]]:
    """归档条目 (名字, 字节) 的有序列表 —— 允许重名, 所以用 list。"""
    with zipfile.ZipFile(archive_path) as archive:
        return [(info.filename, archive.read(info)) for info in archive.infolist()]


def _archive_names(archive_path: str) -> list[str]:
    with zipfile.ZipFile(archive_path) as archive:
        return archive.namelist()


def _archive_entry(archive_path: str, name: str) -> bytes:
    with zipfile.ZipFile(archive_path) as archive:
        return archive.read(name)


def _archive_manifest(archive_path: str) -> dict:
    return json.loads(_archive_entry(archive_path, MANIFEST_ENTRY_NAME).decode("utf-8"))


def _write_pairs(target: str, pairs: list[tuple[str, bytes]]) -> str:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # 重名条目是刻意构造的
        with zipfile.ZipFile(target, "w") as archive:
            for name, payload in pairs:
                archive.writestr(name, payload)
    return target


def _rewrite_archive(
    source: str,
    target: str,
    *,
    manifest_edit=None,
    entry_edit=None,
) -> str:
    """改写一份归档 (用于构造"校验该失败"的归档)。

    ``manifest_edit`` 拿到清单字典可以就地改; ``entry_edit`` 拿到条目字典
    可以增删改。
    """
    entries = dict(_archive_pairs(source))
    manifest = json.loads(entries[MANIFEST_ENTRY_NAME].decode("utf-8"))
    if manifest_edit is not None:
        manifest_edit(manifest)
    entries[MANIFEST_ENTRY_NAME] = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if entry_edit is not None:
        entries = entry_edit(entries)
    return _write_pairs(target, list(entries.items()))


def _with_symlink_entry(source: str, target: str, name: str = "link.txt") -> str:
    """在归档里追加一个符号链接条目 (名字正常, 只能靠 mode 位认出来)。"""
    pairs = _archive_pairs(source)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(target, "w") as archive:
            for entry_name, payload in pairs:
                archive.writestr(entry_name, payload)
            info = zipfile.ZipInfo(name)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "real.txt")
    return target


def _with_duplicate_entry(source: str, target: str, name: str = DATABASE_ENTRY_NAME) -> str:
    """在归档里制造一个重复条目名。"""
    pairs = _archive_pairs(source)
    duplicate = next(payload for entry_name, payload in pairs if entry_name == name)
    return _write_pairs(target, pairs + [(name, duplicate)])


def _archive_course_names(archive_path: str, tmp_path) -> list[str]:
    """从归档里的数据库读出课程名 (用于验证"备份里真的是这份数据")。"""
    target = str(tmp_path / "from-archive.sqlite")
    pathlib.Path(target).write_bytes(_archive_entry(archive_path, DATABASE_ENTRY_NAME))
    database = Database(target)
    try:
        return sorted(course.name for course in Repositories(database).courses.load_all())
    finally:
        database.close()


def _tree_files(root: str) -> dict[str, str]:
    """目录下所有文件的 (相对路径 -> sha256), 用于"没有任何改动"的断言。"""
    out: dict[str, str] = {}
    for base, _dirs, files in os.walk(root):
        for name in sorted(files):
            absolute = os.path.join(base, name)
            relative = os.path.relpath(absolute, root).replace(os.sep, "/")
            out[relative] = hashlib.sha256(pathlib.Path(absolute).read_bytes()).hexdigest()
    return out


def _fixture(tmp_path) -> BackupService:
    """一个"有真实数据"的服务: 已迁移的库 + 四棵树各一份材料。"""
    service = _service(str(tmp_path / "data"))
    _seed_database(service)
    _seed_materials(service)
    return service


# ======================================================================
# 构造: 目录布局与参数校验
# ======================================================================


def test_service_creates_the_full_data_layout(tmp_path):
    service = _service(str(tmp_path / "data"))
    for name in (
        "materials",
        "audio",
        "images",
        "documents",
        "database",
        "logs",
        "backups",
        "temp",
    ):
        assert os.path.isdir(os.path.join(service.data_dir, name)), name


def test_service_absolutises_the_data_dir(tmp_path):
    """相对路径 (含 ``..``) 必须被归一化成绝对路径后再使用。"""
    nested = tmp_path / "nested"
    nested.mkdir()
    service = _service(str(nested / ".." / "data"))
    assert os.path.isabs(service.data_dir)
    assert service.data_dir == str(tmp_path / "data")


@pytest.mark.parametrize("bad", ["", "   ", None, 42])
def test_service_rejects_an_invalid_data_dir(tmp_path, bad):
    with pytest.raises(BackupValidationError) as caught:
        BackupService(bad)
    assert caught.value.code == "INVALID_INPUT"


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_service_rejects_an_invalid_database_filename(tmp_path, bad):
    with pytest.raises(BackupValidationError):
        _service(str(tmp_path / "data"), database_filename=bad)


@pytest.mark.parametrize("bad", ["sub/classroom.sqlite", "sub\\classroom.sqlite", "../x.sqlite"])
def test_service_rejects_a_database_filename_with_a_directory_component(tmp_path, bad):
    with pytest.raises(BackupValidationError) as caught:
        _service(str(tmp_path / "data"), database_filename=bad)
    assert "bare file name" in str(caught.value)


def test_service_rejects_an_unknown_backed_up_dir(tmp_path):
    with pytest.raises(BackupValidationError) as caught:
        _service(str(tmp_path / "data"), backed_up_dirs=("materials", "secret"))
    assert "unknown data directory" in str(caught.value)


@pytest.mark.parametrize("excluded", EXCLUDED_DIRS)
def test_service_rejects_an_excluded_dir_in_backed_up_dirs(tmp_path, excluded):
    """``logs`` / ``temp`` / ``backups`` 不该被备份 —— 想备也不行。"""
    with pytest.raises(BackupValidationError) as caught:
        _service(str(tmp_path / "data"), backed_up_dirs=(excluded,))
    assert "excluded" in str(caught.value)


def test_service_accepts_a_subset_of_backed_up_dirs(tmp_path):
    service = _service(str(tmp_path / "data"), backed_up_dirs=("documents",))
    assert service.backed_up_dirs == ("documents",)


def test_service_defaults_the_backups_dir_to_the_data_layout(tmp_path):
    service = _service(str(tmp_path / "data"))
    assert service.backups_dir == os.path.join(service.data_dir, "backups")


def test_service_honours_a_custom_backups_dir(tmp_path):
    custom = str(tmp_path / "elsewhere")
    service = _service(str(tmp_path / "data"), backups_dir=custom)
    assert service.backups_dir == os.path.abspath(custom)
    result = service.create_backup()
    assert os.path.dirname(result.archive_path) == os.path.abspath(custom)


def test_service_exposes_its_paths(tmp_path):
    service = _service(str(tmp_path / "data"))
    assert service.database_path == os.path.join(
        service.data_dir, "database", DEFAULT_DATABASE_FILENAME
    )
    assert service.layout.root == service.data_dir
    assert service.backed_up_dirs == BACKED_UP_DIRS


def test_service_honours_a_custom_database_filename(tmp_path):
    service = _service(str(tmp_path / "data"), database_filename="clases.sqlite")
    assert service.database_path.endswith(os.path.join("database", "clases.sqlite"))


def test_service_uses_the_given_application_version(tmp_path):
    assert _service(str(tmp_path / "data")).application_version == "1.0.0"


def test_service_resolves_the_application_version_lazily(tmp_path):
    """不传就用项目自己的版本号 (懒加载, 免得在导入期拉起 application 层)。"""
    service = _service(str(tmp_path / "data"), application_version=None)
    assert isinstance(service.application_version, str)
    assert service.application_version.strip()


# ======================================================================
# 重建配置
# ======================================================================


def test_reconstruction_config_records_the_structure(tmp_path):
    config = _service(str(tmp_path / "data")).reconstruction_config()
    assert config["database_filename"] == DEFAULT_DATABASE_FILENAME
    assert config["database_entry"] == DATABASE_ENTRY_NAME
    assert config["manifest_entry"] == MANIFEST_ENTRY_NAME
    assert config["backed_up_dirs"] == list(BACKED_UP_DIRS)
    assert config["excluded_dirs"] == list(EXCLUDED_DIRS)


def test_reconstruction_config_contains_no_absolute_paths(tmp_path):
    """绝对路径是机器相关的, 还会泄露用户名 —— 目标目录由恢复方给出。"""
    service = _service(str(tmp_path / "data"))
    text = json.dumps(service.reconstruction_config(), ensure_ascii=False)
    assert service.data_dir not in text
    assert str(tmp_path) not in text


def test_reconstruction_config_merges_user_config(tmp_path):
    service = _service(str(tmp_path / "data"), config={"asr_model": "medium"})
    assert service.reconstruction_config()["asr_model"] == "medium"


def test_reconstruction_config_cannot_be_overridden_by_user_config(tmp_path):
    """结构字段必须由服务保证正确, 不能被调用方"顺手"改掉。"""
    service = _service(
        str(tmp_path / "data"),
        config={"backed_up_dirs": ["evil"], "database_entry": "evil.sqlite"},
    )
    config = service.reconstruction_config()
    assert config["backed_up_dirs"] == list(BACKED_UP_DIRS)
    assert config["database_entry"] == DATABASE_ENTRY_NAME


# ======================================================================
# create_backup
# ======================================================================


def test_create_backup_writes_the_expected_entries(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    assert sorted(_archive_names(result.archive_path)) == sorted(
        [MANIFEST_ENTRY_NAME, DATABASE_ENTRY_NAME, *DEFAULT_MATERIALS]
    )


def test_create_backup_puts_the_manifest_first(tmp_path):
    """条目顺序固定 -> 归档可以做逐字节比较。"""
    result = _fixture(tmp_path).create_backup()
    assert _archive_names(result.archive_path)[0] == MANIFEST_ENTRY_NAME


def test_create_backup_names_the_archive_from_the_clock(tmp_path):
    result = _fixture(tmp_path).create_backup()
    assert os.path.basename(result.archive_path) == FIXED_NAME


def test_create_backup_appends_a_label_to_the_name(tmp_path):
    result = _fixture(tmp_path).create_backup(label="before-exam")
    assert os.path.basename(result.archive_path) == "backup-20260915T180000Z-before-exam.zip"


def test_create_backup_manifest_records_the_spec_fields(tmp_path):
    result = _fixture(tmp_path).create_backup()
    manifest = _archive_manifest(result.archive_path)
    assert manifest["backup_version"] == BACKUP_VERSION
    assert manifest["schema_version"] == latest_version()
    assert manifest["created_at"] == FIXED_TIME
    assert manifest["application_version"] == "1.0.0"
    assert manifest["file_count"] == 1 + len(DEFAULT_MATERIALS)
    assert len(manifest["database_checksum"]) == 64


def test_create_backup_manifest_checksum_matches_the_snapshot(tmp_path):
    """校验和必须**逐字节**对应归档里那份数据库。"""
    result = _fixture(tmp_path).create_backup()
    manifest = _archive_manifest(result.archive_path)
    digest = hashlib.sha256(_archive_entry(result.archive_path, DATABASE_ENTRY_NAME)).hexdigest()
    assert digest == manifest["database_checksum"]


def test_create_backup_snapshot_contains_the_live_data(tmp_path):
    """快照是"这份数据的副本", 不是"一个空库" —— 用内容而不是字节来断言。"""
    service = _fixture(tmp_path)
    result = service.create_backup()
    assert _archive_course_names(result.archive_path, tmp_path) == _live_course_names(service)


def test_create_backup_manifest_records_every_material_with_a_checksum(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    recorded = {item["path"]: item for item in _archive_manifest(result.archive_path)["material_files"]}
    assert set(recorded) == set(DEFAULT_MATERIALS)
    for relative, content in DEFAULT_MATERIALS.items():
        assert recorded[relative]["size"] == len(content)
        assert recorded[relative]["sha256"] == hashlib.sha256(content).hexdigest()


def test_create_backup_manifest_total_bytes_matches_the_entries(tmp_path):
    """``total_bytes`` 的定义与 ``file_count`` 一致: **不含清单自己**。

    清单不能把自己算进去 —— 否则数字会依赖"清单本身多大"这种自指关系
    (而清单里又写着这个数字)。
    """
    result = _fixture(tmp_path).create_backup()
    manifest = _archive_manifest(result.archive_path)
    with zipfile.ZipFile(result.archive_path) as archive:
        entries = {info.filename: info.file_size for info in archive.infolist()}
    assert manifest["total_bytes"] == entries[DATABASE_ENTRY_NAME] + sum(
        size for name, size in entries.items() if name not in (MANIFEST_ENTRY_NAME, DATABASE_ENTRY_NAME)
    )
    assert manifest["total_bytes"] == manifest["database_size"] + sum(
        item["size"] for item in manifest["material_files"]
    )


def test_create_backup_returns_the_archive_size(tmp_path):
    result = _fixture(tmp_path).create_backup()
    assert result.archive_size == os.path.getsize(result.archive_path)
    assert result.material_file_count == len(DEFAULT_MATERIALS)


def test_create_backup_entry_timestamps_come_from_the_clock(tmp_path):
    """所有条目的 ``date_time`` 都取自清单的 ``created_at`` —— 确定性的一部分。"""
    result = _fixture(tmp_path).create_backup()
    with zipfile.ZipFile(result.archive_path) as archive:
        stamps = {info.date_time for info in archive.infolist()}
    assert stamps == {(2026, 9, 15, 18, 0, 0)}


def test_create_backup_twice_without_overwrite_is_a_conflict(tmp_path):
    service = _fixture(tmp_path)
    service.create_backup()
    with pytest.raises(DuplicateBackupError) as caught:
        service.create_backup()
    assert caught.value.code == "DUPLICATE_BACKUP"
    assert caught.value.detail == {"archive": FIXED_NAME}


def test_create_backup_overwrite_replaces_the_archive(tmp_path):
    service = _fixture(tmp_path)
    first = service.create_backup()
    before = _archive_course_names(first.archive_path, tmp_path)
    _seed_database(service, courses=[("course-3", "Física", "FIS")])
    second = service.create_backup(overwrite=True)
    assert second.archive_path == first.archive_path
    after = _archive_course_names(second.archive_path, tmp_path)
    assert set(after) - set(before) == {"Física"}


def test_create_backup_overwrite_replaces_the_old_file_entirely(tmp_path):
    """覆盖是"换掉整份归档", 不是"往里追加"。"""
    service = _fixture(tmp_path)
    first = service.create_backup()
    original = pathlib.Path(first.archive_path).read_bytes()
    _seed_database(service, courses=[("course-3", "Física", "FIS")])
    service.create_backup(overwrite=True)
    assert pathlib.Path(first.archive_path).read_bytes() != original


@pytest.mark.parametrize("bad", ["before exam", "a/b", "a.b", "a,b", "x" * 41, 2026])
def test_create_backup_rejects_an_invalid_label(tmp_path, bad):
    service = _fixture(tmp_path)
    with pytest.raises(BackupValidationError) as caught:
        service.create_backup(label=bad)
    assert caught.value.code == "INVALID_INPUT"
    assert service.list_backups() == []


def test_create_backup_treats_a_blank_label_as_no_label(tmp_path):
    result = _fixture(tmp_path).create_backup(label="   ")
    assert os.path.basename(result.archive_path) == FIXED_NAME


def test_create_backup_strips_a_label_with_surrounding_spaces(tmp_path):
    result = _fixture(tmp_path).create_backup(label="  before-exam  ")
    assert os.path.basename(result.archive_path).endswith("-before-exam.zip")


def test_create_backup_without_materials_only_has_the_database(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup(include_materials=False)
    assert set(_archive_names(result.archive_path)) == {MANIFEST_ENTRY_NAME, DATABASE_ENTRY_NAME}
    assert result.material_file_count == 0
    assert _archive_manifest(result.archive_path)["file_count"] == 1


@pytest.mark.parametrize("excluded", EXCLUDED_DIRS)
def test_create_backup_excludes_operational_directories(tmp_path, excluded):
    service = _fixture(tmp_path)
    noise = os.path.join(service.data_dir, excluded, "noise.bin")
    pathlib.Path(noise).write_bytes(b"should not be backed up")
    result = service.create_backup()
    assert not any(name.startswith(excluded + "/") for name in _archive_names(result.archive_path))


def test_create_backup_does_not_include_a_previous_backup(tmp_path):
    """备份目录自身绝不进归档 —— 否则归档会随着备份次数指数膨胀。"""
    service = _fixture(tmp_path)
    first = service.create_backup()
    second = _service(service.data_dir, clock=fixed_clock("2026-09-16T09:00:00+00:00")).create_backup()
    for archive_path in (first.archive_path, second.archive_path):
        assert not any(name.endswith(".zip") for name in _archive_names(archive_path))
    assert _archive_names(second.archive_path) == _archive_names(first.archive_path)


def test_create_backup_ignores_pycache_inside_a_material_tree(tmp_path):
    service = _fixture(tmp_path)
    cache = os.path.join(service.data_dir, "documents", "__pycache__")
    os.makedirs(cache, exist_ok=True)
    pathlib.Path(cache, "junk.pyc").write_bytes(b"\x00")
    result = service.create_backup()
    assert not any("__pycache__" in name for name in _archive_names(result.archive_path))


def test_create_backup_keeps_multilingual_names_and_content(tmp_path):
    service = _service(str(tmp_path / "data"))
    _seed_database(service)
    _seed_materials(service, {"documents/apuntes-álgebra.txt": "函数是一种关系。".encode("utf-8")})
    result = service.create_backup()
    payload = _archive_entry(result.archive_path, "documents/apuntes-álgebra.txt")
    assert payload.decode("utf-8") == "函数是一种关系。"


def test_create_backup_creates_a_migrated_database_when_none_exists(tmp_path):
    """空 data_dir 也备份出一份**可用(空)库**, 而不是一份没有数据库的归档。"""
    service = _service(str(tmp_path / "data"))
    result = service.create_backup()
    assert DATABASE_ENTRY_NAME in _archive_names(result.archive_path)
    assert _archive_manifest(result.archive_path)["schema_version"] == latest_version()
    report = validate_archive(result.archive_path)
    assert report.ok, report.errors


def test_create_backup_leaves_no_work_directory(tmp_path):
    service = _fixture(tmp_path)
    service.create_backup()
    assert os.listdir(service.layout.temp) == []


def test_create_backup_leaves_no_partial_file_on_failure(tmp_path, monkeypatch):
    """写归档失败不能留下半个 ``.part`` —— 那会被误当成一份备份。"""

    def boom(self, target, manifest, snapshot, material_entries):
        raise RuntimeError("disk full")

    monkeypatch.setattr(BackupService, "_write_archive", boom)
    service = _fixture(tmp_path)
    with pytest.raises(RuntimeError):
        service.create_backup()
    assert os.listdir(service.backups_dir) == []
    assert os.listdir(service.layout.temp) == []


def test_create_backup_is_deterministic_for_unchanged_data(tmp_path):
    """同一份数据 + 同一时钟 -> 逐字节相同的归档。"""
    service = _fixture(tmp_path)
    first = service.create_backup()
    digest = hashlib.sha256(pathlib.Path(first.archive_path).read_bytes()).hexdigest()
    service.delete_backup(first.archive_path)
    second = service.create_backup()
    assert second.archive_path == first.archive_path
    assert hashlib.sha256(pathlib.Path(second.archive_path).read_bytes()).hexdigest() == digest


def test_create_backup_can_be_called_without_any_database_or_materials(tmp_path):
    """全新安装: 什么都没有也必须能备份 (得到一份空库归档)。"""
    service = _service(str(tmp_path / "fresh"))
    result = service.create_backup()
    assert result.material_file_count == 0
    assert validate_archive(result.archive_path).ok


# ======================================================================
# list_backups
# ======================================================================


def test_list_backups_is_empty_for_a_fresh_data_dir(tmp_path):
    assert _service(str(tmp_path / "data")).list_backups() == []


def test_list_backups_returns_manifest_metadata(tmp_path):
    service = _fixture(tmp_path)
    service.create_backup(label="before-exam")
    (info,) = service.list_backups()
    assert info.name == "backup-20260915T180000Z-before-exam.zip"
    assert info.readable is True
    assert info.error is None
    assert info.created_at == FIXED_TIME
    assert info.schema_version == latest_version()
    assert info.backup_version == BACKUP_VERSION
    assert info.label == "before-exam"
    assert info.file_count == 1 + len(DEFAULT_MATERIALS)
    assert info.size == os.path.getsize(info.path)


def test_list_backups_sorts_by_name(tmp_path):
    service = _fixture(tmp_path)
    for moment in (
        "2026-09-13T09:00:00+00:00",
        "2026-09-11T09:00:00+00:00",
        "2026-09-12T09:00:00+00:00",
    ):
        _service(service.data_dir, clock=fixed_clock(moment)).create_backup()
    assert [info.created_at for info in service.list_backups()] == [
        "2026-09-11T09:00:00+00:00",
        "2026-09-12T09:00:00+00:00",
        "2026-09-13T09:00:00+00:00",
    ]


def test_list_backups_reports_a_corrupt_archive_without_failing(tmp_path):
    """一份坏档不能让整个列表失败 —— 否则用户连"哪些备份是好的"都看不到。"""
    service = _fixture(tmp_path)
    good = service.create_backup()
    pathlib.Path(service.backups_dir, "backup-20260901T000000Z.zip").write_bytes(b"not a zip")
    infos = service.list_backups()
    assert len(infos) == 2
    broken = next(info for info in infos if not info.readable)
    assert broken.error
    healthy = next(info for info in infos if info.readable)
    assert healthy.path == good.archive_path


def test_list_backups_ignores_unrelated_files(tmp_path):
    service = _fixture(tmp_path)
    for name in ("notes.zip", "backup-notes.txt", "readme.md"):
        pathlib.Path(service.backups_dir, name).write_bytes(b"x")
    assert service.list_backups() == []


def test_list_backups_returns_an_empty_list_when_the_directory_is_gone(tmp_path):
    service = _fixture(tmp_path)
    os.rmdir(service.backups_dir)
    assert service.list_backups() == []


# ======================================================================
# validate_archive / validate_backup
# ======================================================================


def test_validate_archive_accepts_a_fresh_backup(tmp_path):
    result = _fixture(tmp_path).create_backup()
    report = validate_archive(result.archive_path)
    assert report.ok is True
    assert report.errors == ()
    assert report.manifest is not None
    assert report.manifest.database_checksum == result.manifest.database_checksum
    assert MANIFEST_ENTRY_NAME in report.entries
    assert report.uncompressed_bytes > 0


def test_validate_archive_is_the_same_as_validate_backup(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    assert service.validate_backup(result.archive_path) == validate_archive(result.archive_path)


def test_validate_archive_creates_nothing_on_disk(tmp_path):
    """校验只看归档内容 —— 不该顺手创建目录或改写任何东西。"""
    service = _fixture(tmp_path)
    result = service.create_backup()
    before = _tree_files(service.data_dir)
    validate_archive(result.archive_path)
    assert _tree_files(service.data_dir) == before


def test_validate_archive_does_not_need_a_data_dir(tmp_path):
    """把归档拷到一个全新的位置也能校验 —— 它不需要 data_dir 参数。"""
    result = _fixture(tmp_path).create_backup()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    copy = str(elsewhere / "backup.zip")
    pathlib.Path(copy).write_bytes(pathlib.Path(result.archive_path).read_bytes())
    assert validate_archive(copy).ok is True


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_validate_archive_rejects_a_blank_path(bad):
    with pytest.raises(BackupValidationError) as caught:
        validate_archive(bad)
    assert caught.value.code == "INVALID_INPUT"


def test_validate_archive_raises_not_found_for_a_missing_file(tmp_path):
    with pytest.raises(BackupNotFoundError) as caught:
        validate_archive(str(tmp_path / "nope.zip"))
    assert caught.value.code == "BACKUP_NOT_FOUND"


def test_validate_archive_reports_a_non_zip_file(tmp_path):
    path = tmp_path / "not-a-zip.zip"
    path.write_text("definitely not a zip", encoding="utf-8")
    report = validate_archive(str(path))
    assert report.ok is False
    assert any("archive:" in error for error in report.errors)
    assert report.manifest is None


def test_validate_archive_reports_a_plain_zip_without_a_manifest(tmp_path):
    """用户随手挑一个 zip 当备份是常态输入 —— 必须被**报告**而不是抛异常。"""
    path = _write_pairs(str(tmp_path / "plain.zip"), [("hello.txt", b"hi")])
    report = validate_archive(path)
    assert report.ok is False
    assert any("manifest" in error for error in report.errors)
    assert report.manifest is None


def test_validate_archive_reports_a_path_traversal_entry(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    evil = _write_pairs(
        str(tmp_path / "evil.zip"),
        _archive_pairs(result.archive_path) + [("../evil.txt", b"pwned")],
    )
    report = validate_archive(evil)
    assert report.ok is False
    assert any("unsafe entry name" in error for error in report.errors)


def test_validate_archive_reports_a_symlink_entry(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    report = validate_archive(_with_symlink_entry(result.archive_path, str(tmp_path / "link.zip")))
    assert report.ok is False
    assert any("symbolic link" in error for error in report.errors)


def test_validate_archive_reports_a_duplicate_entry(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    report = validate_archive(_with_duplicate_entry(result.archive_path, str(tmp_path / "dup.zip")))
    assert report.ok is False
    assert any("duplicate" in error for error in report.errors)


def test_validate_archive_reports_a_missing_database_entry(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    stripped = _rewrite_archive(
        result.archive_path,
        str(tmp_path / "no-db.zip"),
        entry_edit=lambda entries: {
            name: payload for name, payload in entries.items() if name != DATABASE_ENTRY_NAME
        },
    )
    report = validate_archive(stripped)
    assert report.ok is False
    assert any(f"missing entry: {DATABASE_ENTRY_NAME}" in error for error in report.errors)


def test_validate_archive_reports_a_database_checksum_mismatch(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    tampered = _rewrite_archive(
        result.archive_path,
        str(tmp_path / "tampered.zip"),
        manifest_edit=lambda manifest: manifest.__setitem__("database_checksum", "0" * 64),
    )
    report = validate_archive(tampered)
    assert report.ok is False
    assert any("database checksum mismatch" in error for error in report.errors)


def test_validate_archive_reports_a_material_checksum_mismatch(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()

    def tamper(manifest):
        manifest["material_files"][0]["sha256"] = "0" * 64

    report = validate_archive(
        _rewrite_archive(result.archive_path, str(tmp_path / "m.zip"), manifest_edit=tamper)
    )
    assert report.ok is False
    assert any("material checksum mismatch" in error for error in report.errors)


def test_validate_archive_reports_a_declared_file_missing_from_the_archive(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()

    def add_ghost(manifest):
        manifest["material_files"].append(
            {"path": "audio/ghost.mp3", "size": 1, "sha256": "1" * 64}
        )
        manifest["file_count"] = 1 + len(manifest["material_files"])

    report = validate_archive(
        _rewrite_archive(result.archive_path, str(tmp_path / "ghost.zip"), manifest_edit=add_ghost)
    )
    assert report.ok is False
    assert any("declared file missing" in error for error in report.errors)


def test_validate_archive_reports_an_unexpected_entry(tmp_path):
    """归档里出现清单没描述的文件 -> 无法判定它是什么, 拒绝。"""
    service = _fixture(tmp_path)
    result = service.create_backup()
    extra = _rewrite_archive(
        result.archive_path,
        str(tmp_path / "extra.zip"),
        entry_edit=lambda entries: {**entries, "audio/extra.mp3": b"x"},
    )
    report = validate_archive(extra)
    assert report.ok is False
    assert any("unexpected entry" in error for error in report.errors)


def test_validate_archive_reports_a_file_count_mismatch(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    report = validate_archive(
        _rewrite_archive(
            result.archive_path,
            str(tmp_path / "count.zip"),
            manifest_edit=lambda manifest: manifest.__setitem__("file_count", 99),
        )
    )
    assert report.ok is False
    assert any("file_count" in error for error in report.errors)


def test_validate_archive_reports_an_unsupported_backup_version(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    report = validate_archive(
        _rewrite_archive(
            result.archive_path,
            str(tmp_path / "future.zip"),
            manifest_edit=lambda manifest: manifest.__setitem__("backup_version", 99),
        )
    )
    assert report.ok is False
    assert report.manifest is None  # 版本不认识 -> 不解析, 绝不猜
    assert any("manifest" in error for error in report.errors)


@pytest.mark.parametrize(
    "field",
    [
        "backup_version",
        "schema_version",
        "created_at",
        "application_version",
        "file_count",
        "database_checksum",
    ],
)
def test_validate_archive_reports_a_missing_required_manifest_field(tmp_path, field):
    service = _fixture(tmp_path)
    result = service.create_backup()
    report = validate_archive(
        _rewrite_archive(
            result.archive_path,
            str(tmp_path / f"missing-{field}.zip"),
            manifest_edit=lambda manifest: manifest.pop(field),
        )
    )
    assert report.ok is False
    assert any(field in error for error in report.errors)


def test_validate_archive_reports_a_non_json_manifest(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    broken = _rewrite_archive(
        result.archive_path,
        str(tmp_path / "badjson.zip"),
        entry_edit=lambda entries: {**entries, MANIFEST_ENTRY_NAME: b"{not json"},
    )
    report = validate_archive(broken)
    assert report.ok is False
    assert any("JSON" in error for error in report.errors)


def test_validate_archive_keeps_the_manifest_for_a_partial_report(tmp_path):
    """报告里带上解析出来的清单, 便于 UI 显示"这份备份是什么时候的"。"""
    service = _fixture(tmp_path)
    result = service.create_backup()
    tampered = _rewrite_archive(
        result.archive_path,
        str(tmp_path / "t.zip"),
        manifest_edit=lambda manifest: manifest.__setitem__("database_checksum", "0" * 64),
    )
    report = validate_archive(tampered)
    assert report.ok is False
    assert report.manifest is not None
    assert report.manifest.created_at == FIXED_TIME


# ======================================================================
# delete_backup / prune_backups
# ======================================================================


def test_delete_backup_removes_the_file(tmp_path):
    service = _fixture(tmp_path)
    result = service.create_backup()
    assert service.delete_backup(result.archive_path) is True
    assert not os.path.exists(result.archive_path)
    assert service.list_backups() == []


def test_delete_backup_returns_false_for_a_missing_file(tmp_path):
    service = _fixture(tmp_path)
    assert service.delete_backup(str(tmp_path / "nope.zip")) is False


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_delete_backup_rejects_a_blank_path(tmp_path, bad):
    with pytest.raises(BackupValidationError):
        _fixture(tmp_path).delete_backup(bad)


def _three_backups(service: BackupService) -> list[str]:
    paths = []
    for moment in (
        "2026-09-11T09:00:00+00:00",
        "2026-09-12T09:00:00+00:00",
        "2026-09-13T09:00:00+00:00",
    ):
        paths.append(_service(service.data_dir, clock=fixed_clock(moment)).create_backup().archive_path)
    return paths


def test_prune_backups_keeps_the_newest_n(tmp_path):
    service = _fixture(tmp_path)
    old, middle, newest = _three_backups(service)
    removed = service.prune_backups(keep=2)
    assert removed == [old]
    assert [info.path for info in service.list_backups()] == [middle, newest]


def test_prune_backups_removes_nothing_when_under_the_limit(tmp_path):
    service = _fixture(tmp_path)
    _three_backups(service)
    assert service.prune_backups(keep=10) == []
    assert len(service.list_backups()) == 3


def test_prune_backups_keeps_exactly_the_requested_count(tmp_path):
    service = _fixture(tmp_path)
    _three_backups(service)
    service.prune_backups(keep=1)
    assert len(service.list_backups()) == 1


@pytest.mark.parametrize("bad", [0, -1, 1.5, "2", None, True])
def test_prune_backups_rejects_an_invalid_keep(tmp_path, bad):
    """``keep=0`` 等于"一个调用清空所有备份" —— 这种 API 不该存在。"""
    with pytest.raises(BackupValidationError) as caught:
        _fixture(tmp_path).prune_backups(keep=bad)
    assert "keep" in str(caught.value)


def test_prune_backups_works_on_an_empty_directory(tmp_path):
    assert _fixture(tmp_path).prune_backups(keep=3) == []


# ======================================================================
# 模块级便捷函数
# ======================================================================


def test_module_create_backup_splits_constructor_and_action_kwargs(tmp_path):
    data_dir = str(tmp_path / "data")
    _seed_database(_service(data_dir))
    result = create_backup(
        data_dir,
        clock=fixed_clock(FIXED_TIME),
        application_version="1.0.0",
        label="before-exam",
    )
    assert isinstance(result, BackupResult)
    assert os.path.basename(result.archive_path) == "backup-20260915T180000Z-before-exam.zip"


def test_module_create_backup_defaults_to_the_wall_clock(tmp_path):
    """不注入时钟就用真实时间 —— 文件名仍然是合法的 ``backup-*.zip``。"""
    result = create_backup(str(tmp_path / "data"))
    name = os.path.basename(result.archive_path)
    assert name.startswith(BACKUP_FILE_PREFIX) and name.endswith(".zip")


def test_module_list_backups_returns_the_same_as_the_service(tmp_path):
    data_dir = str(tmp_path / "data")
    service = _service(data_dir)
    service.create_backup()
    assert list_backups(data_dir) == service.list_backups()


def test_module_list_backups_rejects_operation_arguments(tmp_path):
    with pytest.raises(BackupValidationError) as caught:
        list_backups(str(tmp_path / "data"), keep=3)
    assert "no operation arguments" in str(caught.value)


def test_module_validate_backup_takes_no_extra_arguments(tmp_path):
    """刻意不接受 ``data_dir`` —— 校验一份备份不该创建 8 个目录。"""
    with pytest.raises(BackupValidationError) as caught:
        validate_backup("x.zip", data_dir=str(tmp_path / "data"))
    assert "no extra arguments" in str(caught.value)


def test_module_validate_backup_matches_validate_archive(tmp_path):
    result = _fixture(tmp_path).create_backup()
    assert validate_backup(result.archive_path) == validate_archive(result.archive_path)


# ======================================================================
# 结果对象
# ======================================================================


def test_backup_info_to_dict_is_json_serialisable(tmp_path):
    info = _fixture(tmp_path)
    info.create_backup()
    payload = json.dumps(info.list_backups()[0].to_dict(), ensure_ascii=False)
    assert FIXED_NAME in payload


def test_backup_validation_to_dict_is_json_serialisable(tmp_path):
    report = validate_archive(_fixture(tmp_path).create_backup().archive_path)
    payload = json.loads(json.dumps(report.to_dict(), ensure_ascii=False))
    assert payload["ok"] is True
    assert payload["manifest"]["created_at"] == FIXED_TIME


def test_backup_result_to_dict_is_json_serialisable(tmp_path):
    result = _fixture(tmp_path).create_backup()
    payload = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))
    assert payload["material_file_count"] == len(DEFAULT_MATERIALS)


def test_restore_result_to_dict_is_json_serialisable():
    result = RestoreResult(
        archive_path="a.zip",
        data_dir="data",
        database_path="data/database/classroom.sqlite",
        replaced_dirs=("database", "audio"),
        material_file_count=2,
        migrated_from=1,
        migrated_to=2,
        warnings=("careful",),
    )
    payload = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))
    assert payload["replaced_dirs"] == ["database", "audio"]
    assert payload["warnings"] == ["careful"]


def test_result_dataclasses_are_frozen():
    """结果对象是值对象 —— 不该在传递过程中被就地改掉。"""
    info = BackupInfo(path="p", name="n", size=1, readable=True)
    with pytest.raises(Exception):
        info.size = 2
    assert isinstance(BackupValidation(archive_path="p", ok=True), BackupValidation)


# ======================================================================
# 分层: 备份层必须走 Database, 不直接碰 sqlite3
# ======================================================================


def _backup_modules() -> list[pathlib.Path]:
    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "backup"
    return sorted(root.glob("*.py"))


def _imported_modules(path: pathlib.Path) -> set[str]:
    """一个 .py 文件里所有 import 的**顶层模块名** (解析 AST, 不靠文本搜索)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_the_layering_guard_actually_sees_the_backup_modules():
    """反向断言: 守卫不能因为"一个文件都没扫到"而空转通过。"""
    names = {path.name for path in _backup_modules()}
    assert {"__init__.py", "archive.py", "errors.py", "manifest.py", "service.py"} <= names


def test_backup_layer_never_imports_sqlite3_directly():
    """数据库只能通过 ``src.persistence.Database`` 访问。

    直连 ``sqlite3`` 会绕过 WAL 配置、事务封装和一致性快照 —— 备份层
    恰恰是最不能绕过这些的地方 (见 ``Database.backup_to`` 的注释)。
    """
    offenders = {
        path.name for path in _backup_modules() if "sqlite3" in _imported_modules(path)
    }
    assert offenders == set()


def test_backup_layer_does_not_import_the_web_layer():
    offenders = {
        path.name
        for path in _backup_modules()
        if any(module.startswith("src.web") for module in _imported_modules(path))
    }
    assert offenders == set()


def test_backup_package_public_surface_is_importable():
    import src.backup as package

    missing = [name for name in package.__all__ if not hasattr(package, name)]
    assert missing == []
