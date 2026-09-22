# -*- coding: utf-8 -*-
"""应用数据目录布局与安全文件原语 (Task 35)。

本模块定义课堂助手在磁盘上的**唯一**数据根 (data_dir) 及其内部结构::

    data/
        materials/   材料注册索引 (每门课程一份 JSON)
        audio/       音频材料副本
        images/      图片材料副本
        documents/   笔记 / PDF / DOCX 材料副本
        database/    SQLite 数据库 (Task 42)
        logs/        运行日志
        backups/     备份归档 (Task 43)
        temp/        临时暂存区 (绝不长期保留)

硬性原则:
- 用户原始文件永远不被就地读写: 所有进入系统的文件必须先复制到
  data_dir 管理的目录。
- 所有写入必须是原子的 (临时文件 + ``os.replace``), 保证崩溃时不会
  留下"写了一半"的文件。
- 任何路径拼接必须经过 :func:`safe_join`, 拒绝 ``..`` 与绝对路径逃逸。
- 用户数据绝不写入 ``src/`` 或 ``tests/``。
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from src.application.errors import ConfigurationError

__all__ = [
    "DATA_LAYOUT_DIRS",
    "CATEGORY_TO_DIR",
    "PROJECT_ROOT_MARKERS",
    "FORBIDDEN_DATA_DIR_NAMES",
    "is_repository_root",
    "guard_data_dir",
    "DataLayout",
    "ensure_data_layout",
    "safe_join",
    "contains_traversal",
    "sanitize_filename",
    "atomic_write_bytes",
    "atomic_copy",
    "remove_quietly",
    "file_sha256",
]

#: 识别"项目仓库根"的标记文件 / 目录。一个目录同时拥有它们, 就当作仓库根。
#: 这是 Task 71.2 的核心判定: 仓库根**绝不允许**作为数据目录 —— 否则测试 /
#: 脚本会在源码树里就地创建 ``database/`` ``materials/`` 等, 污染真实 Pilot 数据。
PROJECT_ROOT_MARKERS: tuple[str, ...] = ("AGENTS.md", "src", "tests")

#: 禁止作为 data_dir 的目录名 (AGENTS.md 硬性规则的可执行版本; 与 config 保持一致)。
FORBIDDEN_DATA_DIR_NAMES: tuple[str, ...] = ("src", "tests")

#: 规范要求的 8 个应用数据目录 (顺序固定, 便于文档与测试断言)。
DATA_LAYOUT_DIRS: tuple[str, ...] = (
    "materials",
    "audio",
    "images",
    "documents",
    "database",
    "logs",
    "backups",
    "temp",
)

#: 材料分类 -> 目标子目录。分类由扩展名唯一决定 (见 material_workflow)。
CATEGORY_TO_DIR: dict[str, str] = {
    "note": "documents",
    "document": "documents",
    "audio": "audio",
    "image": "images",
}


@dataclass(frozen=True)
class DataLayout:
    """已解析的应用数据目录布局。

    所有字段都是**绝对路径**; 通过 :func:`ensure_data_layout` 构造后,
    对应目录一定已存在于磁盘上。
    """

    root: str
    materials: str
    audio: str
    images: str
    documents: str
    database: str
    logs: str
    backups: str
    temp: str

    # ------------------------------------------------------------------

    def bucket_for(self, category: str) -> str:
        """返回某个材料分类对应的存储目录。

        未知分类回落到 ``documents/``, 保证调用方永远不会拿到 None。
        """
        sub = CATEGORY_TO_DIR.get(str(category or "").strip().lower())
        if sub is None:
            return self.documents
        return getattr(self, sub)

    def as_dict(self) -> dict[str, str]:
        return {"root": self.root, **{name: getattr(self, name) for name in DATA_LAYOUT_DIRS}}

    def relative(self, absolute_path: str) -> str:
        """把 data_dir 内的绝对路径转成相对路径 (用于日志/备份清单)。"""
        rel = os.path.relpath(os.path.abspath(absolute_path), self.root)
        return rel.replace(os.sep, "/")


def is_repository_root(path: str) -> bool:
    """``path`` 是否看上去是项目仓库根 (同时含 ``AGENTS.md`` / ``src`` / ``tests``)。

    仅用于 :func:`guard_data_dir` 的判定, 不用于路径解析。
    """
    if not isinstance(path, str) or not path.strip():
        return False
    resolved = os.path.abspath(path)
    if not os.path.isdir(resolved):
        return False
    return all(
        os.path.exists(os.path.join(resolved, marker)) for marker in PROJECT_ROOT_MARKERS
    )


def guard_data_dir(data_dir: str) -> str:
    """拒绝把"源码树 / 仓库根 / src / tests"当成数据目录 (Task 71.2)。

    真实 Pilot 数据必须彻底独立于源码与测试数据。这个守卫在
    :func:`ensure_data_layout` 入口处调用, 因此无论哪条代码路径 (pytest /
    stress test / UI audit / backup drill / 误把 cwd 当 data_dir) 试图在仓库
    内就地建库, 都会立刻得到清晰、用户可读的 :class:`ConfigurationError`,
    而不是在源码树里悄悄留下一堆 ``database/`` ``materials/`` 目录。

    判定 (按优先级):
    1. 非字符串 / 空串 -> ``ValueError`` (与既有契约一致);
    2. 路径的任意一级目录名为 ``src`` 或 ``tests`` -> 拒绝 (含子目录);
    3. 路径就是仓库根 (同时含 ``AGENTS.md`` / ``src`` / ``tests``) -> 拒绝。

    ``classroom-data`` / ``data-pilot`` / ``data-test`` 这类**仓库内的普通子目录**
    (不是仓库根、也不叫 src/tests) 一律放行。
    """
    if not isinstance(data_dir, str) or not data_dir.strip():
        raise ValueError("data_dir must be a non-empty string")
    root = os.path.abspath(data_dir)
    parts = [p for p in root.split(os.sep) if p]
    offending = [p for p in parts if p in FORBIDDEN_DATA_DIR_NAMES]
    if offending:
        raise ConfigurationError(
            f"data_dir must not live inside a '{offending[0]}' directory; "
            "use a dedicated data directory (e.g. classroom-data / data-test) "
            "outside the source tree",
            detail={"data_dir": root, "forbidden": offending[0]},
        )
    if is_repository_root(root):
        raise ConfigurationError(
            "data_dir must not be the project repository root; this would scatter "
            "application data (database/, materials/, ...) across the source tree and "
            "pollute real Pilot data. Use a dedicated data directory instead.",
            detail={"data_dir": root},
        )
    return root


def ensure_data_layout(data_dir: str) -> DataLayout:
    """创建 (必要时) 并返回应用数据目录布局。

    幂等: 重复调用不会破坏已有内容。

    入口处调用 :func:`guard_data_dir`, 确保任何代码路径都不会把数据落到源码
    树 / 仓库根 / ``src`` / ``tests`` 里 (Task 71.2)。
    """
    if not isinstance(data_dir, str) or not data_dir.strip():
        raise ValueError("data_dir must be a non-empty string")
    root = guard_data_dir(data_dir)
    paths: dict[str, str] = {"root": root}
    for name in DATA_LAYOUT_DIRS:
        full = os.path.join(root, name)
        os.makedirs(full, exist_ok=True)
        paths[name] = full
    return DataLayout(**paths)


# ----------------------------------------------------------------------
# 路径安全
# ----------------------------------------------------------------------


def contains_traversal(path: Any) -> bool:
    """路径是否包含 ``..`` 段 (Windows / POSIX 分隔符都识别)。

    只判断 ``..`` 段, 不判断绝对路径 —— 用户合法上传的文件可以位于任意
    目录, 但 ``..`` 段意味着调用方在试图逃离目标目录, 一律拒绝。
    """
    if path is None:
        return False
    raw = str(path).replace("\\", "/")
    return ".." in raw.split("/")


def safe_join(root: str, *parts: str) -> str:
    """把 ``parts`` 安全地拼接到 ``root`` 下。

    - 拒绝包含 ``..`` 的任意 part;
    - 拒绝绝对路径 part (Windows 盘符 / UNC / POSIX 绝对路径);
    - 拼接结果必须仍在 ``root`` 之内, 否则抛 ValueError。
    """
    base = os.path.abspath(root)
    for part in parts:
        if part is None:
            raise ValueError("path part must not be None")
        text = str(part)
        if contains_traversal(text):
            raise ValueError(f"path traversal rejected: {text!r}")
        if os.path.isabs(text) or (len(text) > 1 and text[1] == ":"):
            raise ValueError(f"absolute path part rejected: {text!r}")
        if not text.strip():
            raise ValueError("path part must not be empty")
    candidate = os.path.abspath(os.path.join(base, *[str(p) for p in parts]))
    if candidate != base and not candidate.startswith(base + os.sep):
        raise ValueError(f"resolved path escapes root: {candidate!r}")
    return candidate


#: Windows 上不允许出现在文件名中的字符 (POSIX 上合法, 但产品以 Windows 为先)。
_ILLEGAL_FILENAME_CHARS = frozenset('<>:"/\\|?*')

#: Windows 保留设备名 (不区分大小写, 含扩展名前的部分)。
_RESERVED_STEMS = frozenset(
    ["con", "prn", "aux", "nul"]
    + [f"com{i}" for i in range(1, 10)]
    + [f"lpt{i}" for i in range(1, 10)]
)

#: 单段文件名的长度上限 (NTFS 255 字符; 留出扩展名与安全余量)。
_MAX_STEM_LENGTH = 120


def sanitize_filename(filename: Any, *, fallback: str = "upload") -> str:
    """把用户提供的文件名转成**在本机一定可以落盘**的安全名。

    仅用于暂存 / 临时文件。**绝不用来改写材料的溯源字段**: 原始文件名
    (可能是西语 / 加泰语 / 中文, 也可能含 ``<`` ``>`` 这类在 HTTP 里合法
    但 Windows 上非法的字符) 必须逐字保留, 见
    ``MaterialWorkflowService.register_material(..., filename=...)``。

    规则 (确定性, 无随机成分):
    - 只取基名, 丢弃任何目录成分;
    - 非法字符 (``<>:"/\\|?*``) 与控制字符替换为 ``_``;
    - 去掉结尾的点与空格 (Windows 会静默丢弃它们);
    - 保留扩展名 (它决定材料类型);
    - 空名或 Windows 保留设备名回退为 ``fallback``;
    - 主名截断到 :data:`_MAX_STEM_LENGTH`。
    """
    raw = os.path.basename(str(filename or "").replace("\\", "/")).strip()
    stem, extension = os.path.splitext(raw)

    cleaned = "".join(
        "_" if (ch in _ILLEGAL_FILENAME_CHARS or ord(ch) < 32) else ch
        for ch in stem
    ).strip().rstrip(".")
    if not cleaned or cleaned.lower() in _RESERVED_STEMS:
        cleaned = fallback
    cleaned = cleaned[:_MAX_STEM_LENGTH]

    safe_extension = "".join(
        ch for ch in extension if ch not in _ILLEGAL_FILENAME_CHARS and ord(ch) >= 32
    )
    return cleaned + safe_extension


# ----------------------------------------------------------------------
# 原子文件操作
# ----------------------------------------------------------------------


def file_sha256(path: str, chunk_size: int = 65536) -> str:
    """流式计算文件内容 sha256 (大文件安全)。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _temp_sibling(target: str) -> str:
    """为 ``target`` 生成同目录下的临时文件名 (保证同卷 -> os.replace 原子)。"""
    directory = os.path.dirname(os.path.abspath(target))
    base = os.path.basename(target)
    stem = hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]
    return os.path.join(directory, f".{stem}.part")


def atomic_write_bytes(target: str, payload: bytes) -> None:
    """原子写入字节内容 (写临时文件 -> fsync -> os.replace)。"""
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    tmp = _temp_sibling(target)
    try:
        with open(tmp, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        remove_quietly(tmp)
        raise


def atomic_write_text(target: str, text: str, encoding: str = "utf-8") -> None:
    atomic_write_bytes(target, text.encode(encoding))


def atomic_copy(source: str, target: str) -> None:
    """原子复制文件: 复制到临时文件 -> 校验 -> 移动到目标位置。

    失败时临时文件被清理, 目标位置**不会**留下半成品。
    """
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    tmp = _temp_sibling(target)
    try:
        with open(source, "rb") as src, open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, length=65536)
            dst.flush()
            os.fsync(dst.fileno())
        if os.path.getsize(tmp) != os.path.getsize(source):
            raise OSError(
                f"copy size mismatch: {os.path.getsize(tmp)} != {os.path.getsize(source)}"
            )
        os.replace(tmp, target)
    except BaseException:
        remove_quietly(tmp)
        raise


def remove_quietly(path: Optional[str]) -> bool:
    """删除文件或目录; 不存在或失败都不抛异常。返回是否真的删掉了。"""
    if not path:
        return False
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
            return True
        if os.path.exists(path):
            os.remove(path)
            return True
    except OSError:
        return False
    return False


def clear_directory(path: str) -> int:
    """清空目录内容但保留目录本身; 返回删除的条目数。"""
    removed = 0
    if not os.path.isdir(path):
        return 0
    for name in os.listdir(path):
        if remove_quietly(os.path.join(path, name)):
            removed += 1
    return removed


def iter_files(root: str) -> Iterable[str]:
    """稳定顺序遍历目录下所有文件 (用于备份清单等确定性输出)。"""
    collected: list[str] = []
    for base, dirs, files in os.walk(root):
        dirs.sort()
        for name in sorted(files):
            collected.append(os.path.join(base, name))
    return collected
