# -*- coding: utf-8 -*-
"""备份 / 恢复 / 版本迁移服务 (Task 43)。

对外提供规范点名的四个操作::

    create_backup()
    list_backups()
    restore_backup()
    validate_backup()

归档格式 (``.zip``)::

    manifest.json          清单: 版本 / schema / 时间 / 校验和 / 文件清单
    database.sqlite        SQLite 库的一致性快照
    materials/             材料注册索引 (每门课程一份 JSON)
    audio/                 音频材料副本
    images/                图片材料副本
    documents/             文档 / 笔记材料副本

为什么材料树**按应用自己的相对路径**存放
--------------------------------------------------------------------
规范的建议格式里列了 ``materials/``。本实现保留应用自己的目录名
(``materials/`` ``audio/`` ``images/`` ``documents/``), 原因:

- ``DataLayout`` 已经定义了这四棵树, 恢复就是**纯逆操作** —— 不需要一张
  "归档路径 -> 目标路径"的翻译表。翻译表是 bug 的温床, 而这里可以完全
  没有。
- ``materials/`` 在本应用里是**材料注册索引**(不是材料本体); 材料本体在
  ``audio|images|documents``。把两者混进一个目录会让"哪份文件是什么"
  变得不可判定。
- 清单里记录 ``config.backed_up_dirs``, 所以恢复时"该替换哪些树"由归档
  自己回答, 不靠猜。

``database.sqlite`` 放在归档根目录 —— 这一条**照规范执行**, 因为它让归档
自描述 (不读清单也知道哪个是数据库)。

不备份: ``logs/`` ``temp/`` ``backups/``。运行日志、临时暂存、以及备份目录
自身都不属于"用户数据", 备份它们只会让归档膨胀并引入自引用。

恢复的原子性 (规范: "如果 restore 失败, 原数据库不能被破坏")
--------------------------------------------------------------------
严格按 ``validate -> restore to temporary location -> verify -> atomic replace``::

    1. validate_backup(archive)              归档可信吗?
    2. 解压到 data_dir/temp/restore-*/       绝不直接写正式位置
    3. verify                                校验和 + SQLite integrity_check
                                             + 必要时把旧 schema 迁移到当前
    4. atomic replace                        先把现有数据挪到 previous/,
                                             再把新数据挪进来; 任何一步失败
                                             就把 previous/ 挪回去

第 4 步的"挪"用的是同卷 ``os.replace`` / ``os.rename`` —— 原子, 且不需要
复制大文件。失败路径有专门的测试用真实异常注入验证"原数据完好"。

第 1 步与第 3 步之间还有一道**版本闸门**: 清单或暂存库的 schema 版本高于本
代码支持的版本, 立即拒绝。它必须在第 4 步之前 —— 否则"未来版本的备份"会
静默覆盖掉现在的数据, 而恢复出来的库本代码根本不认识。

``validate_archive`` 的契约: **报告, 不抛异常**
--------------------------------------------------------------------
归档是**不可信输入**, 所以"这份归档哪里有问题"必须能被逐条读出来, 而不是
变成一次异常。因此 ``validate_archive`` 把 ``open_archive`` 与清单解析抛出的
**全部** ``BackupError`` 都收进 ``errors`` 列表, 返回 ``ok=False``。

调用方只需要两种入口:
- 想**知道**能不能恢复 -> 读 ``validate_archive(...).errors``;
- 想**真的**恢复 -> ``restore_backup`` 自己先校验, 不合格就抛
  ``RestoreError``, 并把原因带在 ``detail["errors"]`` 里。

只有"调用方参数本身非法"(空路径 / 文件不存在) 才抛异常 —— 那不是归档的
问题, 是调用的问题。
"""

from __future__ import annotations

import hashlib
import os
import shutil
import zipfile
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from src.application.data_dirs import (
    DATA_LAYOUT_DIRS,
    DataLayout,
    ensure_data_layout,
    file_sha256,
    iter_files,
    remove_quietly,
    safe_join,
)
from src.application.runtime import Clock, utc_now_iso
from src.backup.archive import (
    check_entry_names,
    extract_to,
    open_archive,
    read_entry_bytes,
    read_manifest_bytes,
)
from src.backup.errors import (
    BackupError,
    BackupNotFoundError,
    BackupValidationError,
    DatabaseInUseError,
    DuplicateBackupError,
    ManifestError,
    RestoreError,
    UnsupportedBackupVersionError,
)
from src.backup.manifest import (
    DATABASE_ENTRY_NAME,
    MANIFEST_ENTRY_NAME,
    BackupManifest,
    ManifestFile,
    build_manifest,
    manifest_from_bytes,
    manifest_to_bytes,
)
from src.persistence.database import Database
from src.persistence.migrations import latest_version

__all__ = [
    "DEFAULT_DATABASE_FILENAME",
    "BACKUP_FILE_PREFIX",
    "BACKED_UP_DIRS",
    "EXCLUDED_DIRS",
    "BackupInfo",
    "BackupValidation",
    "BackupResult",
    "RestoreResult",
    "BackupService",
    "validate_archive",
    "create_backup",
    "list_backups",
    "validate_backup",
    "restore_backup",
]

#: 数据库文件名 (Task 42 的约定: ``data/database/classroom.sqlite``)。
DEFAULT_DATABASE_FILENAME = "classroom.sqlite"

#: 归档文件名前缀 (便于 ``list_backups`` 与人工识别)。
BACKUP_FILE_PREFIX = "backup-"

#: 需要备份的数据树 (按此顺序写进归档, 保证确定性)。
BACKED_UP_DIRS: tuple[str, ...] = ("materials", "audio", "images", "documents")

#: 明确不备份的目录 (规范点名: cache / temp / logs / 模型缓存)。
EXCLUDED_DIRS: tuple[str, ...] = ("logs", "temp", "backups")

#: 恢复工作区名前缀 (位于 ``data_dir/temp/`` 下)。
_RESTORE_WORK_PREFIX = "restore-"

#: 恢复失败时用于回滚的暂存目录名 (位于恢复工作区内)。
_PREVIOUS_DIR_NAME = "previous"

#: 复制大文件时的块大小。
_CHUNK = 1 << 16


# ======================================================================
# 结果对象
# ======================================================================


@dataclass(frozen=True)
class BackupInfo:
    """``list_backups`` 的一条记录。

    ``readable=False`` 时 ``error`` 说明原因 —— 一份坏归档**不能**让整个
    列表失败, 否则用户连"哪些备份是好的"都看不到。
    """

    path: str
    name: str
    size: int
    readable: bool
    created_at: Optional[str] = None
    schema_version: Optional[int] = None
    backup_version: Optional[int] = None
    label: Optional[str] = None
    file_count: Optional[int] = None
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "name": self.name,
            "size": self.size,
            "readable": self.readable,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
            "backup_version": self.backup_version,
            "label": self.label,
            "file_count": self.file_count,
            "error": self.error,
        }


@dataclass(frozen=True)
class BackupValidation:
    """``validate_backup`` 的报告 (不抛异常, 把问题逐条列出)。"""

    archive_path: str
    ok: bool
    errors: tuple[str, ...] = ()
    manifest: Optional[BackupManifest] = None
    entries: tuple[str, ...] = ()
    uncompressed_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive_path": self.archive_path,
            "ok": self.ok,
            "errors": list(self.errors),
            "manifest": None if self.manifest is None else self.manifest.to_dict(),
            "entries": list(self.entries),
            "uncompressed_bytes": self.uncompressed_bytes,
        }


@dataclass(frozen=True)
class BackupResult:
    """``create_backup`` 的结果。"""

    archive_path: str
    manifest: BackupManifest
    material_file_count: int
    archive_size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive_path": self.archive_path,
            "manifest": self.manifest.to_dict(),
            "material_file_count": self.material_file_count,
            "archive_size": self.archive_size,
        }


@dataclass(frozen=True)
class RestoreResult:
    """``restore_backup`` 的结果。"""

    archive_path: str
    data_dir: str
    database_path: str
    replaced_dirs: tuple[str, ...] = ()
    material_file_count: int = 0
    migrated_from: Optional[int] = None
    migrated_to: Optional[int] = None
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "archive_path": self.archive_path,
            "data_dir": self.data_dir,
            "database_path": self.database_path,
            "replaced_dirs": list(self.replaced_dirs),
            "material_file_count": self.material_file_count,
            "migrated_from": self.migrated_from,
            "migrated_to": self.migrated_to,
            "warnings": list(self.warnings),
        }


# ======================================================================
# 纯校验 (不依赖任何 data_dir, 无副作用)
# ======================================================================


def validate_archive(archive_path: str) -> BackupValidation:
    """完整校验一份归档, 返回报告 (问题逐条列出, 不抛异常)。

    检查项与规范一一对应: zip 完整性 / manifest / 数据库校验和 /
    期望路径 / 无路径穿越。另外检查材料文件的校验和。

    **不需要 data_dir, 也不创建任何目录** —— 校验只看归档内容。
    """
    if not isinstance(archive_path, str) or not archive_path.strip():
        raise BackupValidationError("archive_path must be a non-empty string")
    if not os.path.isfile(archive_path):
        raise BackupNotFoundError(f"backup not found: {archive_path!r}")

    errors: list[str] = []
    manifest: Optional[BackupManifest] = None
    entries: tuple[str, ...] = ()
    total = 0

    try:
        with open_archive(archive_path) as archive:
            entries = check_entry_names(archive.namelist())
            total = sum(int(info.file_size) for info in archive.infolist())

            try:
                manifest = manifest_from_bytes(read_manifest_bytes(archive))
            except BackupError as exc:
                # 刻意用基类: 用户随手挑一个 zip 当备份是**常态**输入,
                # 此时 ``read_manifest_bytes`` 抛的是 ``MissingArchiveEntryError``
                # 而不是 ``ManifestError``。只捕 ManifestError 会让
                # "校验一份备份" 在遇到普通 zip 时直接抛异常, 违背本函数
                # "把问题逐条列出来" 的契约。
                errors.append(f"manifest: {exc}")
                return BackupValidation(
                    archive_path=archive_path,
                    ok=False,
                    errors=tuple(errors),
                    manifest=None,
                    entries=entries,
                    uncompressed_bytes=total,
                )

            # 数据库条目 + 校验和
            payload = read_entry_bytes(archive, DATABASE_ENTRY_NAME, required=False)
            if payload is None:
                errors.append(f"missing entry: {DATABASE_ENTRY_NAME}")
            else:
                digest = hashlib.sha256(payload).hexdigest()
                if digest != manifest.database_checksum:
                    errors.append(
                        "database checksum mismatch: "
                        f"manifest={manifest.database_checksum[:12]}... "
                        f"archive={digest[:12]}..."
                    )

            # 材料文件: 清单声明了就必须在, 且校验和一致
            declared = {item.path for item in manifest.material_files}
            present = set(entries)
            for missing in sorted(declared - present):
                errors.append(f"declared file missing from archive: {missing}")
            for item in manifest.material_files:
                if item.path not in present:
                    continue
                data = read_entry_bytes(archive, item.path, required=False)
                if data is None:
                    errors.append(f"declared file missing from archive: {item.path}")
                elif hashlib.sha256(data).hexdigest() != item.sha256:
                    errors.append(f"material checksum mismatch: {item.path}")

            # 归档里出现了既不是清单/数据库、又没被清单声明的文件
            allowed = {MANIFEST_ENTRY_NAME, DATABASE_ENTRY_NAME} | declared
            for unexpected in sorted(present - allowed):
                errors.append(
                    f"unexpected entry not described by the manifest: {unexpected}"
                )

            try:
                manifest.validate_self_consistency()
            except ManifestError as exc:
                errors.append(f"manifest: {exc}")
    except BackupError as exc:
        # 同样刻意用基类: ``open_archive`` 会抛 ``UnsafeArchivePathError``
        # (路径穿越 / 符号链接 / 重复条目) 与 ``ArchiveTooLargeError``
        # (zip 炸弹), 它们都不是 ``CorruptedBackupError`` 的子类。只捕后者
        # 会让"校验一份恶意归档"直接抛异常 —— 而这恰恰是最该被**报告**的
        # 情形: 调用方需要看到 ok=False 与原因, 而不是一个异常。
        errors.append(f"archive: {exc}")

    return BackupValidation(
        archive_path=archive_path,
        ok=not errors,
        errors=tuple(errors),
        manifest=manifest,
        entries=entries,
        uncompressed_bytes=total,
    )


# ======================================================================
# 服务
# ======================================================================


class BackupService:
    """一个 data_dir 的备份 / 恢复门面。

    ``clock`` 可注入 —— 归档文件名与 ``created_at`` 都来自它, 因此测试可以
    做到逐字节确定 (同一时钟值 -> 同一文件名 -> 同一归档字节)。
    """

    def __init__(
        self,
        data_dir: str,
        *,
        database_filename: str = DEFAULT_DATABASE_FILENAME,
        backups_dir: Optional[str] = None,
        clock: Optional[Clock] = None,
        application_version: Optional[str] = None,
        config: Optional[Mapping[str, Any]] = None,
        backed_up_dirs: Sequence[str] = BACKED_UP_DIRS,
    ) -> None:
        if not isinstance(data_dir, str) or not data_dir.strip():
            raise BackupValidationError("data_dir must be a non-empty string")
        if not isinstance(database_filename, str) or not database_filename.strip():
            raise BackupValidationError("database_filename must be a non-empty string")
        if os.path.basename(database_filename) != database_filename:
            raise BackupValidationError(
                f"database_filename must be a bare file name, got {database_filename!r}"
            )
        for name in backed_up_dirs:
            if name not in DATA_LAYOUT_DIRS:
                raise BackupValidationError(
                    f"unknown data directory in backed_up_dirs: {name!r} "
                    f"(known: {list(DATA_LAYOUT_DIRS)})"
                )
            if name in EXCLUDED_DIRS:
                raise BackupValidationError(
                    f"{name!r} is an excluded directory and cannot be backed up"
                )

        self._layout: DataLayout = ensure_data_layout(data_dir)
        self._database_filename = database_filename
        self._backups_dir = (
            os.path.abspath(backups_dir) if backups_dir else self._layout.backups
        )
        self._clock: Clock = clock if clock is not None else utc_now_iso
        self._application_version = application_version
        self._config = dict(config or {})
        self._backed_up_dirs = tuple(backed_up_dirs)

    # ------------------------------------------------------------------
    # 路径与版本
    # ------------------------------------------------------------------

    @property
    def layout(self) -> DataLayout:
        return self._layout

    @property
    def data_dir(self) -> str:
        return self._layout.root

    @property
    def database_path(self) -> str:
        return os.path.join(self._layout.database, self._database_filename)

    @property
    def backups_dir(self) -> str:
        return self._backups_dir

    @property
    def backed_up_dirs(self) -> tuple[str, ...]:
        return self._backed_up_dirs

    @property
    def application_version(self) -> str:
        if self._application_version is not None:
            return self._application_version
        # P1-4: 不在此处 import ``src.application.workspace``, 以打破
        # application <-> backup 的互指。包级 ``src.__version__`` 与
        # ``APPLICATION_VERSION`` 由 test_bootstrap 钉为一致, 可直接回落。
        from src import __version__

        return __version__

    def reconstruction_config(self) -> dict[str, Any]:
        """规范要求的 "configuration required for reconstruction"。

        只记录**结构信息**, 不记录绝对路径 (机器相关, 且会泄露用户名)。
        目标目录在恢复时由调用方给出。
        """
        config: dict[str, Any] = {
            "data_dir_name": os.path.basename(self._layout.root),
            "database_filename": self._database_filename,
            "database_relative_path": f"database/{self._database_filename}",
            "database_entry": DATABASE_ENTRY_NAME,
            "manifest_entry": MANIFEST_ENTRY_NAME,
            "data_layout_dirs": list(DATA_LAYOUT_DIRS),
            "backed_up_dirs": list(self._backed_up_dirs),
            "excluded_dirs": list(EXCLUDED_DIRS),
        }
        # 调用方提供的额外配置 (Task 44 的 AppConfig 重建相关子集) 合并进来,
        # 但**不允许**覆盖上面的结构字段 —— 那些必须由本服务保证正确。
        for key, value in self._config.items():
            config.setdefault(key, value)
        return config

    # ==================================================================
    # create_backup
    # ==================================================================

    def create_backup(
        self,
        *,
        label: Optional[str] = None,
        overwrite: bool = False,
        include_materials: bool = True,
    ) -> BackupResult:
        """创建一个 ``.zip`` 备份。

        幂等: 同一时钟值不会产生第二份文件 (已存在则报 ``CONFLICT``,
        除非显式 ``overwrite=True``)。
        """
        label = self._validate_label(label)
        os.makedirs(self._backups_dir, exist_ok=True)
        target = self._archive_path(label)
        if os.path.exists(target) and not overwrite:
            raise DuplicateBackupError(
                f"backup already exists: {os.path.basename(target)!r} "
                "(pass overwrite=True to replace it)",
                detail={"archive": os.path.basename(target)},
            )

        work = os.path.join(self._layout.temp, "backup-work")
        remove_quietly(work)
        os.makedirs(work, exist_ok=True)
        try:
            # 1) 数据库一致性快照
            snapshot = os.path.join(work, DATABASE_ENTRY_NAME)
            self._snapshot_database(snapshot)

            # 2) 材料文件清单 (确定性顺序)
            material_files, material_entries = self._collect_materials(
                include=include_materials
            )

            # 3) 清单 (file_count / total_bytes 由 build_manifest 算出来)
            manifest = build_manifest(
                schema_version=self._database_schema_version(),
                created_at=str(self._clock()),
                application_version=self.application_version,
                database_checksum=file_sha256(snapshot),
                database_size=os.path.getsize(snapshot),
                database_filename=self._database_filename,
                material_files=material_files,
                config=self.reconstruction_config(),
                label=label,
            )

            # 4) 写归档 (先写 .part, 再原子改名 —— 失败不会留下半个归档)
            staging = target + ".part"
            remove_quietly(staging)
            try:
                self._write_archive(staging, manifest, snapshot, material_entries)
                os.replace(staging, target)
            except BaseException:
                remove_quietly(staging)
                raise
        finally:
            remove_quietly(work)

        return BackupResult(
            archive_path=target,
            manifest=manifest,
            material_file_count=manifest.material_file_count,
            archive_size=os.path.getsize(target),
        )

    # ==================================================================
    # list_backups
    # ==================================================================

    def list_backups(self) -> list[BackupInfo]:
        """列出备份目录里的归档 (按文件名排序 = 按时间排序)。

        一份坏归档只会在自己的 ``error`` 里体现, **不会**让列表失败。
        """
        if not os.path.isdir(self._backups_dir):
            return []
        names = sorted(
            name
            for name in os.listdir(self._backups_dir)
            if name.startswith(BACKUP_FILE_PREFIX) and name.lower().endswith(".zip")
        )
        out: list[BackupInfo] = []
        for name in names:
            path = os.path.join(self._backups_dir, name)
            if not os.path.isfile(path):
                continue
            size = os.path.getsize(path)
            try:
                manifest = self._peek_manifest(path)
            except Exception as exc:  # noqa: BLE001 - 列表不能因单份坏档而失败
                out.append(
                    BackupInfo(
                        path=path,
                        name=name,
                        size=size,
                        readable=False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            out.append(
                BackupInfo(
                    path=path,
                    name=name,
                    size=size,
                    readable=True,
                    created_at=manifest.created_at,
                    schema_version=manifest.schema_version,
                    backup_version=manifest.backup_version,
                    label=manifest.label,
                    file_count=manifest.file_count,
                )
            )
        return out

    # ==================================================================
    # validate_backup
    # ==================================================================

    def validate_backup(self, archive_path: str) -> BackupValidation:
        """``validate_archive`` 的实例方法形式 (内容与实例状态无关)。"""
        return validate_archive(archive_path)

    # ==================================================================
    # restore_backup
    # ==================================================================

    def restore_backup(
        self,
        archive_path: str,
        *,
        restore_materials: bool = True,
        migrate: bool = True,
    ) -> RestoreResult:
        """把归档恢复到这个 ``data_dir``。

        失败时**原数据保持完好** (规范硬性要求)。实现见模块 docstring。
        """
        validation = self.validate_backup(archive_path)
        if not validation.ok:
            raise RestoreError(
                "backup failed validation, refusing to restore: "
                + "; ".join(validation.errors[:5]),
                detail={"errors": list(validation.errors[:10])},
            )
        manifest = validation.manifest
        if manifest is None:  # validate_archive 保证 ok=True 时非 None
            raise RestoreError("validated archive has no manifest")

        # 来自**更新版本**的备份必须在碰任何现有数据之前就拒绝。
        #
        # 清单里的 ``schema_version`` 正是为这件事存在的。以前这里只在
        # "需要升版"时检查版本 (``manifest.schema_version < latest_version()``),
        # 于是"未来版本写的备份"会**静默恢复成功** —— 用户以为数据回来了,
        # 实际上库里是一份本代码不认识的 schema, 而且原数据已经被替换掉。
        # 这类错误必须在第一个破坏性步骤之前发生, 所以放在这里。
        if manifest.schema_version > latest_version():
            raise UnsupportedBackupVersionError(
                f"backup schema version {manifest.schema_version} is newer than "
                f"this build supports ({latest_version()}); refusing to restore "
                "before touching any existing data"
            )

        work = os.path.join(
            self._layout.temp, _RESTORE_WORK_PREFIX + self._archive_stem(archive_path)
        )
        remove_quietly(work)
        os.makedirs(work, exist_ok=True)
        previous = os.path.join(work, _PREVIOUS_DIR_NAME)
        os.makedirs(previous, exist_ok=True)

        warnings: list[str] = []
        try:
            # ---- 2) restore to a temporary location -------------------
            with open_archive(archive_path) as archive:
                extract_to(archive, work, entries=[DATABASE_ENTRY_NAME])
                if restore_materials:
                    for tree in self._trees_to_restore(manifest):
                        prefix = tree + "/"
                        members = [
                            entry for entry in validation.entries if entry.startswith(prefix)
                        ]
                        if members:
                            extract_to(archive, work, entries=members)

            # ---- 3) verify -------------------------------------------
            staged_db = os.path.join(work, DATABASE_ENTRY_NAME)
            if not os.path.isfile(staged_db):
                raise RestoreError(f"staged database is missing: {staged_db!r}")
            if file_sha256(staged_db) != manifest.database_checksum:
                raise RestoreError(
                    "staged database checksum does not match the manifest "
                    "(the archive changed between validation and extraction)"
                )
            self._verify_staged_database(staged_db)

            migrated_from: Optional[int] = None
            migrated_to: Optional[int] = None
            if migrate and manifest.schema_version < latest_version():
                migrated_from = manifest.schema_version
                self._migrate_staged_database(staged_db)
                migrated_to = latest_version()

            if restore_materials:
                self._verify_staged_materials(work, manifest)

            # ---- 4) atomic replace -----------------------------------
            replaced = self._replace_database(staged_db, previous)
            if restore_materials:
                replaced += self._replace_materials(
                    work, manifest, previous, validation.entries, warnings
                )
        except BaseException as exc:
            self._rollback(previous, warnings)
            remove_quietly(work)
            if isinstance(exc, BackupError):
                # 已经是结构化备份错误 -> 原样抛。
                #
                # 这里曾写成 ``(RestoreError, UnsupportedBackupVersionError)``,
                # 于是 ``DatabaseInUseError`` (它的**兄弟**, 不是子类) 会被
                # 包成 ``RestoreError`` —— 用户看到的是泛泛的
                # ``STORAGE_RESTORE_FAILED``, 而真正可执行的那条提示
                # ("先把应用关掉") 只存在于嵌套的消息字符串里。
                # 备份层所有错误都带稳定 code, 没有再包一层的理由。
                raise
            raise RestoreError(
                f"restore failed and was rolled back: {exc}", cause=exc
            ) from exc

        remove_quietly(work)
        return RestoreResult(
            archive_path=archive_path,
            data_dir=self._layout.root,
            database_path=self.database_path,
            replaced_dirs=tuple(replaced),
            material_file_count=manifest.material_file_count,
            migrated_from=migrated_from,
            migrated_to=migrated_to,
            warnings=tuple(warnings),
        )

    # ==================================================================
    # 管理辅助
    # ==================================================================

    def delete_backup(self, archive_path: str) -> bool:
        """删除一份归档 (不存在返回 False, 不抛错)。"""
        if not isinstance(archive_path, str) or not archive_path.strip():
            raise BackupValidationError("archive_path must be a non-empty string")
        absolute = os.path.abspath(archive_path)
        if not os.path.isfile(absolute):
            return False
        try:
            os.remove(absolute)
        except OSError:
            return False
        return True

    def prune_backups(self, *, keep: int = 10) -> list[str]:
        """只保留最新的 ``keep`` 份 (按文件名 = 时间顺序), 返回被删掉的路径。

        ``keep`` 必须 >= 1 —— 允许 0 就等于"一个调用清空所有备份",
        这种 API 不该存在。
        """
        if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
            raise BackupValidationError(f"keep must be an integer >= 1, got {keep!r}")
        backups = self.list_backups()
        removed: list[str] = []
        for info in backups[: max(0, len(backups) - keep)]:
            if self.delete_backup(info.path):
                removed.append(info.path)
        return removed

    # ==================================================================
    # 内部: 命名
    # ==================================================================

    @staticmethod
    def _validate_label(label: Optional[str]) -> Optional[str]:
        if label is None:
            return None
        if not isinstance(label, str):
            raise BackupValidationError("label must be a string or None")
        text = label.strip()
        if not text:
            return None
        safe = "".join(ch for ch in text if ch.isalnum() or ch in ("-", "_"))
        if not safe or safe != text:
            raise BackupValidationError(
                f"label must contain only letters, digits, '-' and '_': {label!r}"
            )
        if len(safe) > 40:
            raise BackupValidationError("label must be at most 40 characters")
        return safe

    def _archive_path(self, label: Optional[str]) -> str:
        stamp = self._stamp(str(self._clock()), label)
        return os.path.join(self._backups_dir, f"{BACKUP_FILE_PREFIX}{stamp}.zip")

    @staticmethod
    def _stamp(created_at: str, label: Optional[str]) -> str:
        """把 ``created_at`` 变成文件名安全的标识 (确定性)。

        ``2026-09-15T18:30:12+00:00`` -> ``20260915T183012Z``。
        非 ISO 输入不会被静默丢掉: 退化成数字白名单, 保证文件名仍可用
        且不同时间不会撞名。
        """
        text = str(created_at)
        if len(text) >= 19 and text[4] == "-" and text[10] == "T" and text[13] == ":":
            stamp = (
                f"{text[0:4]}{text[5:7]}{text[8:10]}"
                f"T{text[11:13]}{text[14:16]}{text[17:19]}Z"
            )
        else:
            digits = "".join(ch for ch in text if ch.isdigit())
            stamp = digits[:14].ljust(14, "0") if digits else "unknown"
        return stamp + (f"-{label}" if label else "")

    @staticmethod
    def _archive_stem(archive_path: str) -> str:
        base = os.path.basename(archive_path)
        stem = base[:-4] if base.lower().endswith(".zip") else base
        return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)

    # ==================================================================
    # 内部: 写
    # ==================================================================

    def _snapshot_database(self, target: str) -> None:
        """把活库快照到 ``target``。

        活库不存在时**建一个迁移到最新版的空库**再快照 —— 这样清单里的
        ``database_checksum`` 永远有意义, 恢复出来也一定是一个可用(空)库,
        而不是"什么都没有"。这比生成一份没有数据库的归档更有用。
        """
        live = self.database_path
        if not os.path.isfile(live):
            fresh = Database(target)
            try:
                fresh.migrate()
            finally:
                fresh.close()
            return
        source = Database(live)
        try:
            source.backup_to(target)
        finally:
            source.close()

    def _database_schema_version(self) -> int:
        live = self.database_path
        if not os.path.isfile(live):
            return latest_version()
        database = Database(live)
        try:
            return database.schema_version()
        finally:
            database.close()

    def _collect_materials(
        self, *, include: bool
    ) -> tuple[tuple[ManifestFile, ...], tuple[tuple[str, str], ...]]:
        """收集材料文件。

        返回 ``(清单记录, (归档条目名, 源绝对路径) 列表)``; 两者顺序一致,
        都是确定性的 (:func:`iter_files` 保证)。
        """
        if not include:
            return (), ()
        records: list[ManifestFile] = []
        entries: list[tuple[str, str]] = []
        for tree in self._backed_up_dirs:
            root = getattr(self._layout, tree)
            if not os.path.isdir(root):
                continue
            for absolute in iter_files(root):
                if "__pycache__" in absolute.replace("\\", "/").split("/"):
                    continue
                relative = os.path.relpath(absolute, root).replace(os.sep, "/")
                records.append(
                    ManifestFile(
                        path=f"{tree}/{relative}",
                        size=os.path.getsize(absolute),
                        sha256=file_sha256(absolute),
                    )
                )
                entries.append((f"{tree}/{relative}", absolute))
        records.sort(key=lambda f: f.path)
        entries.sort(key=lambda pair: pair[0])
        return tuple(records), tuple(entries)

    def _write_archive(
        self,
        target: str,
        manifest: BackupManifest,
        snapshot: str,
        material_entries: Sequence[tuple[str, str]],
    ) -> None:
        """写归档。

        两个关键点:
        - 条目顺序固定 (清单 -> 数据库 -> 材料, 材料按路径排序);
        - 所有条目的 ``date_time`` 都取自清单的 ``created_at``。

        因此"同一份数据 + 同一时钟值 -> 逐字节相同的归档"成立, 这是可以
        被测试直接断言的确定性。
        """
        stamp = _zip_datetime(manifest.created_at)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            _write_bytes_entry(archive, MANIFEST_ENTRY_NAME, manifest_to_bytes(manifest), stamp)
            _write_file_entry(archive, DATABASE_ENTRY_NAME, snapshot, stamp)
            for entry_name, source in material_entries:
                _write_file_entry(archive, entry_name, source, stamp)

    # ==================================================================
    # 内部: 读 / 校验
    # ==================================================================

    @staticmethod
    def _peek_manifest(archive_path: str) -> BackupManifest:
        """只读清单 (不做 CRC / 体积校验), 供列表使用。"""
        with open_archive(
            archive_path, check_integrity=False, check_limits=False
        ) as archive:
            return manifest_from_bytes(read_manifest_bytes(archive))

    def _restore_plan(self, manifest: BackupManifest) -> tuple[tuple[str, ...], bool]:
        """返回 ``(要替换的材料树, 该列表是否来自归档自己的声明)``。

        为什么要把"是否声明"也返回: 两种情形的正确行为不同 ——

        - 归档**声明**了它的目录构成 (``config.backed_up_dirs``): 那就忠实
          还原。某棵树在归档里是空的, 恢复后它就该是空的 (否则"恢复"就不是
          恢复到那个时间点, 而是"恢复 + 保留后来的新增", 不可预期)。
        - 归档**没**声明 (老归档 / 手工做的归档): 只替换它**确实含有文件**的
          树, 其余不动并给出 warning。信息不足时选择不破坏。

        两条路径都只产出 :data:`BACKED_UP_DIRS` 的子集, 绝不凭空造目录名。
        """
        declared = manifest.config.get("backed_up_dirs")
        if isinstance(declared, (list, tuple)) and declared:
            trees = tuple(
                str(name)
                for name in declared
                if str(name) in DATA_LAYOUT_DIRS and str(name) not in EXCLUDED_DIRS
            )
            if trees:
                return trees, True
        return self._backed_up_dirs, False

    def _trees_to_restore(self, manifest: BackupManifest) -> tuple[str, ...]:
        """该替换哪些材料树 (``_restore_plan`` 的简化视图)。"""
        return self._restore_plan(manifest)[0]

    def _verify_staged_database(self, staged_db: str) -> None:
        """解压出来的库必须能被打开、自身一致, 且**不是来自更新的 schema**。

        版本检查不能只信清单: 清单是可以被改的 (它就是个 JSON 文件)。
        真正的判据是**库里迁移台账的最大版本号**。
        """
        probe = Database(staged_db)
        try:
            current = probe.schema_version()
            if current > latest_version():
                raise UnsupportedBackupVersionError(
                    f"backup schema version {current} is newer than this build "
                    f"supports ({latest_version()})"
                )
            if not probe.is_healthy():
                raise RestoreError(
                    "staged database failed its integrity check",
                    detail={"violations": probe.foreign_key_violations()[:10]},
                )
        finally:
            probe.close()

    def _migrate_staged_database(self, staged_db: str) -> None:
        """把旧 schema 的暂存库迁移到当前版本 (规范: old schema -> current)。"""
        database = Database(staged_db)
        try:
            current = database.schema_version()
            if current > latest_version():
                raise UnsupportedBackupVersionError(
                    f"backup schema version {current} is newer than this build "
                    f"supports ({latest_version()})"
                )
            database.migrate()
        finally:
            database.close()

    def _verify_staged_materials(self, work: str, manifest: BackupManifest) -> None:
        """逐个复核解压出来的材料文件。

        ``validate_archive`` 已经查过一遍; 这里再查一遍是因为"校验"与
        "落盘"之间有时间差 —— 归档可能在这中间被换掉。对不上就**拒绝恢复**,
        而不是带着坏文件继续 (那会得到一份引用不存在材料的数据库)。
        """
        for item in manifest.material_files:
            staged = os.path.join(work, *item.path.split("/"))
            if not os.path.isfile(staged):
                raise RestoreError(
                    f"material file missing after extraction: {item.path}",
                    detail={"path": item.path},
                )
            if file_sha256(staged) != item.sha256:
                raise RestoreError(
                    f"staged material file failed its checksum: {item.path}",
                    detail={"path": item.path},
                )

    # ==================================================================
    # 内部: 原子替换与回滚
    # ==================================================================

    def _replace_database(self, staged_db: str, previous: str) -> list[str]:
        """原子替换活库。

        两个关键细节:

        1. **必须先清掉 ``-wal`` / ``-shm``**。WAL 模式下这两个文件里可能存着
           旧库已提交的数据; 只替换主库文件会让 SQLite 把旧 WAL 重放到新库上
           —— 那是最坏的一类数据损坏 (静默混合两份数据)。
        2. **Windows 上被占用的文件无法替换**。只要还有句柄持有活库,
           ``os.replace`` 就报 ``WinError 5``。这里把它翻译成
           :class:`DatabaseInUseError` 并给出可执行的提示。

        这是恢复流程里**第一个破坏性步骤**, 所以它失败 == 什么都没改。
        """
        live = self.database_path
        os.makedirs(os.path.dirname(live), exist_ok=True)
        keep = os.path.join(previous, "database")
        os.makedirs(keep, exist_ok=True)

        # 1) 保底副本 (回滚用)
        if os.path.isfile(live):
            shutil.copy2(live, os.path.join(keep, self._database_filename))
        for suffix in ("-wal", "-shm"):
            side = live + suffix
            if os.path.isfile(side):
                shutil.copy2(side, os.path.join(keep, self._database_filename + suffix))

        # 2) 清掉 WAL 边车文件, 再原子换主库
        for suffix in ("-wal", "-shm"):
            remove_quietly(live + suffix)
        try:
            os.replace(staged_db, live)
        except OSError as exc:
            raise _translate_replace_error(exc, live) from exc
        return ["database"]

    def _replace_materials(
        self,
        work: str,
        manifest: BackupManifest,
        previous: str,
        entries: Sequence[str],
        warnings: list[str],
    ) -> list[str]:
        """替换材料树: 现有 -> ``previous/``, 新的 -> 正式位置。"""
        trees, declared = self._restore_plan(manifest)
        replaced: list[str] = []
        for tree in trees:
            live_dir = getattr(self._layout, tree)
            staged_dir = os.path.join(work, tree)
            has_entries = any(entry.startswith(tree + "/") for entry in entries)
            if not declared and not has_entries:
                # 归档没声明目录构成, 又没带这棵树的文件 -> 不动它。
                warnings.append(
                    f"backup does not describe {tree}/, leaving it untouched"
                )
                continue

            keep = os.path.join(previous, tree)
            if os.path.isdir(live_dir):
                try:
                    os.rename(live_dir, keep)  # 同卷 -> 原子
                except OSError as exc:
                    raise _translate_replace_error(exc, live_dir) from exc
            try:
                if os.path.isdir(staged_dir):
                    os.rename(staged_dir, live_dir)
                else:
                    # 归档声明了这棵树但没有文件 -> 忠实还原成"空目录"。
                    os.makedirs(live_dir, exist_ok=True)
            except BaseException:
                # 把这一棵树挪回去, 然后让上层统一回滚
                if os.path.isdir(keep) and not os.path.exists(live_dir):
                    try:
                        os.rename(keep, live_dir)
                    except OSError as exc:
                        warnings.append(f"could not move {tree}/ back: {exc}")
                raise
            replaced.append(tree)
        return replaced

    def _rollback(self, previous: str, warnings: list[str]) -> None:
        """把 ``previous/`` 里的内容挪回原位。

        用 ``os.replace`` / ``os.rename`` 而**不是**复制: 恢复路径上再出一次
        "复制到一半失败" 会让原数据也坏掉, 那就彻底违背了
        "如果 restore 失败, 原数据库不能被破坏"。``previous/`` 与正式位置
        同在 ``data_dir`` 下, 因此必然是同一卷, rename 是原子的。

        本方法绝不抛异常 —— 它本身就在错误路径上。
        """
        if not os.path.isdir(previous):
            return
        keep_db = os.path.join(previous, "database")
        if os.path.isdir(keep_db):
            live = self.database_path
            for suffix in ("", "-wal", "-shm"):
                source = os.path.join(keep_db, self._database_filename + suffix)
                if not os.path.isfile(source):
                    continue
                destination = live + suffix
                try:
                    os.replace(source, destination)
                except OSError:
                    try:
                        shutil.copy2(source, destination)
                    except OSError as exc:
                        warnings.append(f"rollback of database failed: {exc}")
        for name in sorted(os.listdir(previous)):
            if name == "database" or name not in DATA_LAYOUT_DIRS:
                continue
            source = os.path.join(previous, name)
            if not os.path.isdir(source):
                continue
            live_dir = getattr(self._layout, name)
            try:
                remove_quietly(live_dir)
                os.rename(source, live_dir)
            except OSError as exc:
                warnings.append(f"rollback of {name}/ failed: {exc}")


# ======================================================================
# zip 条目辅助
# ======================================================================


def _translate_replace_error(exc: OSError, target: str) -> BackupError:
    """把"替换失败"翻译成有意义的错误。

    Windows 上"文件被占用"表现为 ``PermissionError`` / ``WinError 5``
    (拒绝访问) 或 ``WinError 32`` (正被另一个进程使用)。这类失败**不是**
    磁盘坏了, 而是"恢复是离线操作, 你得先把库关掉" —— 所以给一条能照做的
    提示, 而不是裸 ``PermissionError``。
    """
    winerror = getattr(exc, "winerror", None)
    if isinstance(exc, PermissionError) or winerror in (5, 32):
        return DatabaseInUseError(
            "cannot replace the live data because a file is still open: "
            f"{target!r}. Close the application (all database connections) "
            "before restoring a backup.",
            detail={"path": target, "winerror": winerror},
            cause=exc,
        )
    return RestoreError(
        f"cannot replace {target!r}: {exc}", detail={"path": target}, cause=exc
    )


def _zip_datetime(created_at: str) -> tuple[int, int, int, int, int, int]:
    """ISO 时间戳 -> zip 的 ``date_time`` (DOS 格式, 2 秒精度)。

    解析失败退回 zip 的纪元 ``1980-01-01 00:00:00`` —— 不猜时间, 但也不
    因此让备份失败。
    """
    text = str(created_at)
    try:
        if len(text) >= 19 and text[4] == "-" and text[10] == "T":
            year = int(text[0:4])
            month = int(text[5:7])
            day = int(text[8:10])
            hour = int(text[11:13])
            minute = int(text[14:16])
            second = int(text[17:19])
            if 1980 <= year <= 2107 and 1 <= month <= 12 and 1 <= day <= 31:
                return (year, month, day, hour % 24, minute % 60, second % 60)
    except (ValueError, IndexError):
        pass
    return (1980, 1, 1, 0, 0, 0)


def _entry_info(name: str, stamp: tuple[int, int, int, int, int, int]) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(filename=name, date_time=stamp)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16  # 普通文件, 不是符号链接
    return info


def _write_bytes_entry(
    archive: zipfile.ZipFile,
    name: str,
    payload: bytes,
    stamp: tuple[int, int, int, int, int, int],
) -> None:
    archive.writestr(_entry_info(name, stamp), payload)


def _write_file_entry(
    archive: zipfile.ZipFile,
    name: str,
    source: str,
    stamp: tuple[int, int, int, int, int, int],
) -> None:
    """流式写入一个文件条目 —— 不把整个文件读进内存。

    课堂音频动辄几百 MB; 一次性 ``read()`` 会让备份把内存吃满。
    """
    with open(source, "rb") as handle, archive.open(_entry_info(name, stamp), "w") as sink:
        shutil.copyfileobj(handle, sink, length=_CHUNK)


# ======================================================================
# 模块级便捷函数 (规范点名的四个操作)
# ======================================================================

#: ``BackupService.__init__`` 的参数名 —— 用来把构造参数与操作参数分开。
_CONSTRUCTOR_KEYS = frozenset(
    {
        "database_filename",
        "backups_dir",
        "clock",
        "application_version",
        "config",
        "backed_up_dirs",
    }
)


def _split(kwargs: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(构造参数, 操作参数)`` —— 两类关键字混在一个 ``**kwargs`` 里。"""
    options = {k: v for k, v in kwargs.items() if k in _CONSTRUCTOR_KEYS}
    action = {k: v for k, v in kwargs.items() if k not in _CONSTRUCTOR_KEYS}
    return options, action


def create_backup(data_dir: str, **kwargs: Any) -> BackupResult:
    """``BackupService.create_backup`` 的便捷入口 (``data_dir`` 必填)。"""
    options, action = _split(kwargs)
    return BackupService(data_dir, **options).create_backup(**action)


def list_backups(data_dir: str, **kwargs: Any) -> list[BackupInfo]:
    """``BackupService.list_backups`` 的便捷入口。"""
    options, action = _split(kwargs)
    if action:
        raise BackupValidationError(
            f"list_backups takes no operation arguments, got {sorted(action)}"
        )
    return BackupService(data_dir, **options).list_backups()


def validate_backup(archive_path: str, **kwargs: Any) -> BackupValidation:
    """``validate_archive`` 的便捷入口。

    刻意**不接受** ``data_dir`` —— 校验只看归档内容, 不需要也不应该触碰
    任何数据目录 (否则"校验一份备份"会顺手创建 8 个目录)。
    """
    options, action = _split(kwargs)
    if options or action:
        raise BackupValidationError(
            f"validate_backup takes no extra arguments, got {sorted(kwargs)}"
        )
    return validate_archive(archive_path)


def restore_backup(archive_path: str, data_dir: str, **kwargs: Any) -> RestoreResult:
    """``BackupService.restore_backup`` 的便捷入口。"""
    options, action = _split(kwargs)
    return BackupService(data_dir, **options).restore_backup(archive_path, **action)
