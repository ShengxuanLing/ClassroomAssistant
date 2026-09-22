# 备份与恢复

备份层在 `src/backup/`，**没有 HTTP 端点，也没有命令行入口**——它是一层 Python API。
所有对外入口都在 `src/backup/__init__.py`：

```python
from src.backup import (
    create_backup, list_backups, restore_backup,
    validate_backup, BackupService,
)
```

---

## 1. 为什么不能直接复制 `.sqlite` 文件

数据库跑在 **WAL** 模式。WAL 模式下，**已提交**的数据可能还躺在 `-wal` 边车文件里，
主库文件本身是过期的。直接 `shutil.copy` 会静默备份出一份**丢数据**的库，
而且看起来一切正常——这是最危险的一类错误。

正确做法只有一个：SQLite 的**在线备份 API**（`sqlite3.Connection.backup`）。
它自己处理 WAL，并且在整个过程中对源库保持一致读视图。这不是"更优雅"，
而是唯一不会丢已提交数据的做法。所以 `Database.backup_to()` 是唯一的快照入口。

### 一个真实的陷阱：事务开着时快照会永久挂起

在线备份 API 要在**源连接**上取得读锁；而 `BEGIN IMMEDIATE` 已经在这个连接上
持有写锁。SQLite 于是等一个自己永远放不掉的锁，`backup_step` 一直返回
`SQLITE_BUSY`，Python 的循环里没有超时，进程**永久挂死**。

实测：连"只 `BEGIN`、一个字都没写"的空事务也会挂。这是本机服务里最坏的失败形态
——请求永不返回，而且写锁一直被占着，之后每个写操作都要先等 5 秒 `busy_timeout`
再失败。

因此 `Database.backup_to()` 会**显式拒绝**：

```python
if self._depth > 0:
    raise TransactionError(
        "cannot snapshot the database while a transaction is open on this connection..."
    )
```

它**绝不**替调用方 commit——那会把一次本该回滚的事务变成永久落库，比挂死更危险。
备份层本身的做法是另开一个 `Database` 实例再快照，那条路径不受影响。

回归测试：`tests/test_hardening_database_safety.py::TestBackupSafety::
test_backup_refuses_while_a_transaction_is_open`。

## 2. 创建备份

```python
result = create_backup("classroom-data", label="before-exam")

result.archive_path            # classroom-data/backups/classroom-<时间戳>-before-exam.zip
result.manifest                # BackupManifest
result.material_file_count     # 归档里的材料文件数
result.archive_size
```

参数：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `label` | `None` | 会写进归档名与清单；只允许字母数字和 `-` `_` |
| `overwrite` | `False` | 同名归档已存在时默认报 `DUPLICATE_BACKUP`（`CONFLICT`） |
| `include_materials` | `True` | 关掉则只备份数据库 |

创建过程（四步，全部确定性）：

1. **数据库一致性快照** → 工作目录下的 `database.sqlite`。
   活库不存在时会**建一个迁移到最新版的空库**再快照，这样清单里的
   `database_checksum` 永远有意义，恢复出来也一定是一个可用（空）库。
2. **材料文件清单**，按固定顺序收集。
3. **写清单**（`file_count` / `total_bytes` 由 `build_manifest` 算出来）。
4. **写归档**：先写 `.part`，成功后 `os.replace` 原子改名——失败不会留下半个归档。

## 3. 归档格式

```text
<classroom>-<时间戳>[-<label>].zip
├── manifest.json      归档的自我描述
└── database.sqlite    自包含的单文件库（WAL 已合并）
└── materials/...      材料文件，路径与 data_dir 下的相对路径一致
```

`manifest.json`：

| 字段 | 说明 |
| --- | --- |
| `backup_version` | 归档格式版本，当前 `1`（`MAX_SUPPORTED_BACKUP_VERSION = 1`） |
| `schema_version` | 数据库 schema 版本（当前 `2`） |
| `created_at` | 来自**注入的 Clock**，不是 `datetime.now()` |
| `application_version` | 生成归档的程序版本 |
| `file_count` / `total_bytes` | 材料文件数与总字节数 |
| `database_checksum` / `database_size` / `database_filename` | 数据库快照的 SHA-256 与大小 |
| `label` | 可选标签 |
| `material_files[]` | 每个材料文件的 `path` / `size` / `sha256` |
| `config` | 重建所需的配置片段 |

归档是**确定性**的：条目顺序固定、JSON 规范化、时间戳来自注入的 Clock。
同一个时钟值不会产生两份不同的归档。

## 4. 校验

```python
validation = validate_backup("classroom-data/backups/x.zip")
validation.ok          # 是否可安全恢复
validation.errors      # 逐条列出问题，不抛异常
validation.manifest
validation.uncompressed_bytes
```

`validate_backup` **不抛异常**，把问题逐条列出来——这样"一份坏归档"不会让整个
备份列表失败，用户仍然能看到"哪些备份是好的"。

### 归档是不可信输入

归档会被当成**外部输入**处理，不是"自己生成的东西所以一定安全"。已实现的防护：

| 攻击面 | 防护 |
| --- | --- |
| 路径穿越 | 条目路径规范化后必须落在目标目录内，`../` 一律拒绝 |
| 符号链接 | 拒绝 symlink 条目（不能靠链接把写操作引到归档外） |
| 重复条目 | 同名条目只允许出现一次 |
| zip bomb | `MAX_UNCOMPRESSED_BYTES = 8 GiB`，`MAX_COMPRESSION_RATIO = 2000` |
| 损坏数据 | 逐个条目校验 CRC 与清单里的 SHA-256 |

## 5. 恢复

```python
result = restore_backup(
    "classroom-data/backups/x.zip",
    "classroom-data",          # 目标 data_dir
    restore_materials=True,
    migrate=True,
)

result.replaced_dirs         # 被替换掉的目录
result.material_file_count
result.migrated_from / result.migrated_to
result.warnings
```

流程：

1. **先校验**。校验不过直接 `RestoreError`，**不碰任何现有数据**。
2. **版本前置检查**。归档的 `schema_version` 高于本代码支持的上限时，
   在**第一个破坏性步骤之前**就拒绝（`UnsupportedBackupVersionError`）。
   以前这里只在"需要升版"时检查，于是"未来版本写的备份"会**静默恢复成功**——
   用户以为数据回来了，实际上库里是一份本代码不认识的 schema，而原数据已经被
   替换掉了。
3. **保底副本**：恢复前先给现有数据留一份回滚用的副本。
4. **清掉 WAL 边车文件**（`-wal` / `-shm`），再**原子换主库**。
   活库上还开着 SQLite 连接时 `os.replace` 会被拒（Windows 上是 `WinError 5`），
   这时翻成 `DatabaseInUseError` 而不是含糊的 `OSError`。
5. 需要时执行迁移，并把 `migrated_from` / `migrated_to` 报出来。

**失败时原数据保持完好**——这是规范里的硬性要求，也是恢复流程的设计目标。

## 6. 列表

```python
for info in list_backups("classroom-data"):
    print(info.name, info.created_at, info.schema_version, info.readable, info.error)
```

`readable=False` 时 `error` 说明原因。一条坏归档不会让整个列表失败。

## 7. 已知限制（明确说明，不掩饰）

1. **归档默认落在 `data_dir/backups/` 里**，也就是和被备份的数据**在同一个目录**。
   整目录级的丢失（误删、磁盘故障）会连备份一起带走。
   异地/异盘复制需要你自己做。这条由
   `tests/test_hardening_backup_drill.py::TestRestorableNow::
   test_archive_lives_inside_the_data_dir_by_default` 断言记录。
2. **恢复的业务数据覆盖范围已完整。** Task 48–55 把业务对象真正接进 SQLite 之后，
   运行时 `data_dir/database/` **有活库**，归档里的数据库快照是**含全部业务对象**的。
   恢复之后课程注册表、知识点、审核记录、学生状态、作答、评估、计划、路径**全部按
   内容寻址 ID 逐字段恢复**；材料文件逐字节一致且可达。
   `tests/test_hardening_backup_drill.py`（含 `TestDrillGap`，类名保留、**断言方向已反转**
   为真正的恢复验收）与 `tests/test_production_gate.py::TestFileDatabaseConsistency`
   钉住这件事。原先"恢复出空库"的缺口断言按 spec 55.14 改写为 PASS。
3. **没有自动/定时备份。** 需要你自己调 `create_backup`。
4. **没有增量备份。** 每次都是全量快照。
