# -*- coding: utf-8 -*-
"""用户数据保护: 备份 / 恢复 / 版本迁移 (Task 43)。

用法
--------------------------------------------------------------------

::

    from src.backup import BackupService, fixed_clock

    service = BackupService("data", clock=fixed_clock("2026-09-15T18:00:00+00:00"))

    result = service.create_backup()              # -> .zip
    for info in service.list_backups():           # 列表 (坏档只影响自己那条)
        print(info.name, info.readable, info.created_at)

    report = service.validate_backup(result.archive_path)
    assert report.ok, report.errors

    restored = service.restore_backup(result.archive_path)

分层
--------------------------------------------------------------------

::

    src/backup/
        errors.py     结构化错误 (命名即映射到 8 个用户可见错误码)
        manifest.py   manifest.json 的定义、构造与严格校验
        archive.py    zip 读写 + 路径穿越 / 符号链接 / zip 炸弹防护
        service.py    四个操作 + 原子替换 + 回滚

依赖方向是**单向**的::

    src/backup  ->  src/persistence   (一致性快照 + schema 版本 + 健康检查)
    src/backup  ->  src/application.data_dirs / runtime   (目录布局 + 可注入时钟)

``src.persistence`` 与领域层**不知道**备份层的存在。备份是持久化之上的一层,
不是它的一部分。

为什么需要这一层而不是"复制一下文件"
--------------------------------------------------------------------

1. **WAL 模式让"复制数据库文件"是错的**。已提交的数据可能还在 ``-wal``
   里, 主库文件是过期的 —— 直接复制会静默备份出一份丢数据的库, 而且看起来
   一切正常。因此用 SQLite 的在线备份 API (``Database.backup_to``)。
2. **归档是不可信输入**。用户会从别处拷来备份文件, 所以必须防路径穿越、
   符号链接条目、重复条目名和 zip 炸弹。
3. **恢复失败不能毁数据**。严格走
   ``validate -> 解压到临时位置 -> 复核 -> 原子替换``, 失败即回滚。
"""

from __future__ import annotations

from src.application.runtime import Clock, fixed_clock, utc_now_iso
from src.backup.archive import (
    MAX_ARCHIVE_ENTRIES,
    MAX_COMPRESSION_RATIO,
    MAX_UNCOMPRESSED_BYTES,
    check_archive_limits,
    check_entry_names,
    is_safe_archive_name,
    is_symlink_entry,
    open_archive,
)
from src.backup.errors import (
    ArchiveTooLargeError,
    BackupError,
    BackupErrorCode,
    BackupNotFoundError,
    BackupValidationError,
    ChecksumMismatchError,
    CorruptedBackupError,
    DatabaseInUseError,
    DuplicateBackupError,
    ManifestError,
    MissingArchiveEntryError,
    RestoreError,
    UnsafeArchivePathError,
    UnsupportedBackupVersionError,
)
from src.backup.manifest import (
    BACKUP_VERSION,
    DATABASE_ENTRY_NAME,
    MANIFEST_ENTRY_NAME,
    MAX_SUPPORTED_BACKUP_VERSION,
    REQUIRED_MANIFEST_FIELDS,
    BackupManifest,
    ManifestFile,
    build_manifest,
    manifest_from_bytes,
    manifest_to_bytes,
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
    restore_backup,
    validate_archive,
    validate_backup,
)

__all__ = [
    # 服务
    "BackupService",
    "create_backup",
    "list_backups",
    "validate_backup",
    "validate_archive",
    "restore_backup",
    # 结果对象
    "BackupInfo",
    "BackupResult",
    "BackupValidation",
    "RestoreResult",
    # 清单
    "BACKUP_VERSION",
    "MAX_SUPPORTED_BACKUP_VERSION",
    "MANIFEST_ENTRY_NAME",
    "DATABASE_ENTRY_NAME",
    "REQUIRED_MANIFEST_FIELDS",
    "BackupManifest",
    "ManifestFile",
    "build_manifest",
    "manifest_from_bytes",
    "manifest_to_bytes",
    # 归档原语
    "MAX_ARCHIVE_ENTRIES",
    "MAX_UNCOMPRESSED_BYTES",
    "MAX_COMPRESSION_RATIO",
    "open_archive",
    "check_entry_names",
    "check_archive_limits",
    "is_safe_archive_name",
    "is_symlink_entry",
    # 常量
    "DEFAULT_DATABASE_FILENAME",
    "BACKUP_FILE_PREFIX",
    "BACKED_UP_DIRS",
    "EXCLUDED_DIRS",
    # 错误
    "BackupError",
    "BackupErrorCode",
    "BackupValidationError",
    "BackupNotFoundError",
    "DuplicateBackupError",
    "CorruptedBackupError",
    "ManifestError",
    "ChecksumMismatchError",
    "UnsafeArchivePathError",
    "ArchiveTooLargeError",
    "MissingArchiveEntryError",
    "DatabaseInUseError",
    "RestoreError",
    "UnsupportedBackupVersionError",
    # 时钟 (便于测试构造确定性备份)
    "Clock",
    "fixed_clock",
    "utc_now_iso",
]
