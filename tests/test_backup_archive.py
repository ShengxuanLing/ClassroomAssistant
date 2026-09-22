# -*- coding: utf-8 -*-
"""Task 43 —— zip 归档的结构校验与安全防护测试。

规范要求恢复前必须验证::

    zip integrity / manifest / database checksum / expected paths
    no path traversal

与 zip 结构有关的那部分 (完整性 / 路径 / 炸弹) 在本文件覆盖; 校验和与清单
语义在 ``test_backup_manifest.py`` 与 ``test_backup_service.py``。

为什么这些检查一个都不能省
--------------------------------------------------------------------
备份文件是**可以被用户搬运、编辑、从别处拷来的**, 因此是不可信输入。
本文件对每一类威胁都有真实构造出来的恶意归档 (不是 mock):

- 路径穿越 ``../evil.txt``
- 符号链接条目 (名字完全正常, 只能靠 Unix mode 位认出来)
- 重复条目名 (校验读到一个、解压写入另一个)
- zip 炸弹 (条目数 / 解压体积 / 压缩比)
- CRC 损坏 (传输中坏掉的归档)
"""

from __future__ import annotations

import os
import pathlib
import stat
import warnings
import zipfile

import pytest

from src.backup.archive import (
    MAX_ARCHIVE_ENTRIES,
    MAX_COMPRESSION_RATIO,
    MAX_UNCOMPRESSED_BYTES,
    check_archive_limits,
    check_entry_names,
    extract_to,
    is_safe_archive_name,
    is_symlink_entry,
    open_archive,
    read_entry_bytes,
    read_manifest_bytes,
    verify_zip_integrity,
)
from src.backup.errors import (
    ArchiveTooLargeError,
    CorruptedBackupError,
    MissingArchiveEntryError,
    UnsafeArchivePathError,
)
from src.backup.manifest import MANIFEST_ENTRY_NAME

#: 一个够长、够独特的载荷, 便于在归档字节里定位。
_PAYLOAD = b"PAYLOAD-FOR-CRC-TEST-0123456789-ABCDEFGHIJ"


# ======================================================================
# 构造辅助
# ======================================================================


def _write_zip(path: str, entries: dict[str, bytes], *, stored: bool = False) -> str:
    """写一个普通 zip (值 -> 条目字节)。"""
    compression = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(path, "w", compression=compression) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return path


def _write_raw_zip(path: str, entries: list[tuple[str, bytes]]) -> str:
    """写一个允许**重复条目名**的 zip (``dict`` 做不到这一点)。

    ``zipfile`` 在写重复名时会发 ``UserWarning`` —— 这里正是**故意**要造一个
    这样的归档, 所以把警告静音, 而不是让整个测试会话都带着它。
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as archive:
            for name, payload in entries:
                archive.writestr(name, payload)
    return path


def _write_symlink_zip(path: str, name: str = "link.txt", target: str = "real.txt") -> str:
    """写一个含符号链接条目的 zip (靠 Unix mode 位表达)。"""
    info = zipfile.ZipInfo(name)
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, target)
    return path


def _corrupt_stored_payload(path: str, payload: bytes = _PAYLOAD) -> str:
    """把 STORED 条目的载荷改掉一个字节 (CRC 就不再匹配)。

    这是"归档在传输中坏掉"的最小复现: 结构完好、CRC 对不上。
    """
    raw = pathlib.Path(path).read_bytes()
    index = raw.find(payload)
    assert index >= 0, "payload not found verbatim in the archive"
    flipped = bytes(byte ^ 0xFF for byte in payload)
    pathlib.Path(path).write_bytes(raw[:index] + flipped + raw[index + len(payload) :])
    return path


# ======================================================================
# is_safe_archive_name
# ======================================================================


@pytest.mark.parametrize(
    "name",
    [
        "manifest.json",
        "database.sqlite",
        "materials/course-1.json",
        "audio/clase1.mp3",
        "documents/apuntes-álgebra.txt",
        "a/b/c/d/e.txt",
        "a b c.txt",
        "2026-09-15.txt",
    ],
)
def test_is_safe_archive_name_accepts_relative_paths(name):
    assert is_safe_archive_name(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "",
        "../evil.txt",
        "a/../evil.txt",
        "a/..",
        "..",
        "/etc/passwd",
        "~/secrets",
        "C:/Windows/system32",
        "C:evil.txt",
        "a\\b.txt",
        "a//b.txt",
        "a/./b.txt",
        "./a.txt",
        "a/",
        "a\x01b.txt",
        "a\nb.txt",
    ],
)
def test_is_safe_archive_name_rejects_escaping_or_malformed_names(name):
    assert is_safe_archive_name(name) is False


@pytest.mark.parametrize("name", [None, 42, b"bytes", ["list"]])
def test_is_safe_archive_name_rejects_non_strings(name):
    assert is_safe_archive_name(name) is False


def test_is_safe_archive_name_rejects_a_normalised_name_that_differs():
    """归一化后必须等于原值 —— 挡住 ``a/b/`` 这类"看起来在内、实际在外"。"""
    assert is_safe_archive_name("a/b/") is False


# ======================================================================
# check_entry_names
# ======================================================================


def test_check_entry_names_returns_the_safe_names_in_order():
    assert check_entry_names(["b.txt", "a.txt"]) == ("b.txt", "a.txt")


def test_check_entry_names_accepts_an_empty_archive():
    assert check_entry_names([]) == ()


def test_check_entry_names_skips_directory_entries():
    """目录条目 (``dir/``) 不是文件, 不进入待解压清单。"""
    assert check_entry_names(["materials/", "materials/c1.json"]) == ("materials/c1.json",)


def test_check_entry_names_tolerates_a_repeated_directory_entry():
    """重复的**目录**条目无害: 它不承载数据, 也没有"校验读到哪个"的问题。"""
    assert check_entry_names(["d/", "d/"]) == ()


def test_check_entry_names_rejects_a_root_directory_entry():
    with pytest.raises(UnsafeArchivePathError) as caught:
        check_entry_names(["/"])
    assert caught.value.code == "STORAGE_BACKUP_UNSAFE_PATH"


def test_check_entry_names_rejects_an_unsafe_directory_entry():
    with pytest.raises(UnsafeArchivePathError):
        check_entry_names(["../"])


def test_check_entry_names_rejects_an_unsafe_file_entry():
    with pytest.raises(UnsafeArchivePathError) as caught:
        check_entry_names(["../evil.txt"])
    assert caught.value.detail == {"entry": "../evil.txt"}


def test_check_entry_names_rejects_duplicate_file_entries():
    """校验与解压可能看到不同的数据 —— 一律拒绝。"""
    with pytest.raises(UnsafeArchivePathError) as caught:
        check_entry_names(["a.txt", "a.txt"])
    assert "duplicate" in str(caught.value)
    assert caught.value.detail == {"entry": "a.txt"}


def test_check_entry_names_rejects_an_empty_name():
    with pytest.raises(UnsafeArchivePathError):
        check_entry_names([""])


# ======================================================================
# is_symlink_entry
# ======================================================================


def test_is_symlink_entry_detects_a_unix_symlink_mode():
    info = zipfile.ZipInfo("link.txt")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    assert is_symlink_entry(info) is True


def test_is_symlink_entry_accepts_a_regular_file_mode():
    info = zipfile.ZipInfo("file.txt")
    info.external_attr = 0o644 << 16
    assert is_symlink_entry(info) is False


def test_is_symlink_entry_accepts_a_missing_unix_mode():
    """Windows 工具写的归档常常没有 Unix mode 位 (0) —— 那不是符号链接。"""
    assert is_symlink_entry(zipfile.ZipInfo("file.txt")) is False


def test_is_symlink_entry_accepts_a_directory_mode():
    info = zipfile.ZipInfo("dir/")
    info.external_attr = (stat.S_IFDIR | 0o755) << 16
    assert is_symlink_entry(info) is False


# ======================================================================
# check_archive_limits
# ======================================================================


def test_limits_are_positive_and_finite():
    assert MAX_ARCHIVE_ENTRIES > 0
    assert MAX_UNCOMPRESSED_BYTES > 0
    assert MAX_COMPRESSION_RATIO > 1


def test_check_archive_limits_accepts_a_realistic_archive(tmp_path):
    path = _write_zip(str(tmp_path / "ok.zip"), {"audio/a.mp3": b"x" * 100_000})
    with zipfile.ZipFile(path) as archive:
        assert check_archive_limits(archive) == 100_000


def test_check_archive_limits_rejects_too_many_entries(tmp_path):
    path = _write_zip(str(tmp_path / "many.zip"), {f"f{i}.txt": b"x" for i in range(5)})
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(ArchiveTooLargeError) as caught:
            check_archive_limits(archive, max_entries=4)
    assert caught.value.code == "STORAGE_BACKUP_TOO_LARGE"
    assert caught.value.detail == {"entries": 5, "limit": 4}


def test_check_archive_limits_rejects_an_oversized_single_entry(tmp_path):
    path = _write_zip(str(tmp_path / "big.zip"), {"audio/a.mp3": b"x" * 1000})
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(ArchiveTooLargeError) as caught:
            check_archive_limits(archive, max_uncompressed_bytes=999)
    assert caught.value.detail["size"] == 1000


def test_check_archive_limits_rejects_an_oversized_total(tmp_path):
    """单个条目都在限内, 加起来超限 —— 也必须拦住。"""
    path = _write_zip(
        str(tmp_path / "sum.zip"),
        {"a.bin": os.urandom(200), "b.bin": os.urandom(200)},
    )
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(ArchiveTooLargeError) as caught:
            check_archive_limits(archive, max_uncompressed_bytes=300)
    assert caught.value.detail["total_bytes"] == 400


def test_check_archive_limits_rejects_a_high_compression_ratio(tmp_path):
    """zip 炸弹的典型特征: 声明体积很小, 解压后极大。"""
    path = _write_zip(str(tmp_path / "bomb.zip"), {"bomb.bin": b"\0" * 1_000_000})
    with zipfile.ZipFile(path) as archive:
        info = archive.infolist()[0]
        assert info.compress_size < info.file_size  # 前提: 真的压得动
        with pytest.raises(ArchiveTooLargeError) as caught:
            check_archive_limits(archive, max_compression_ratio=10)
    assert "ratio" in str(caught.value)
    assert caught.value.detail == {"entry": "bomb.bin"}


def test_check_archive_limits_accepts_an_incompressible_entry(tmp_path):
    """压不动的条目压缩比 < 1, 不能被误判成炸弹。"""
    path = _write_zip(str(tmp_path / "random.zip"), {"a.bin": os.urandom(4096)})
    with zipfile.ZipFile(path) as archive:
        check_archive_limits(archive, max_compression_ratio=2)


# ======================================================================
# open_archive
# ======================================================================


def test_open_archive_yields_a_usable_zip(tmp_path):
    path = _write_zip(str(tmp_path / "ok.zip"), {"a.txt": b"hi"})
    with open_archive(path) as archive:
        assert archive.namelist() == ["a.txt"]


def test_open_archive_closes_the_zip_on_exit(tmp_path):
    """句柄必须被释放 —— 否则 Windows 上后续的 ``os.replace`` 会失败。"""
    path = _write_zip(str(tmp_path / "ok.zip"), {"a.txt": b"hi"})
    with open_archive(path) as archive:
        pass
    with pytest.raises(ValueError):
        archive.read("a.txt")


@pytest.mark.parametrize("path", ["", "   "])
def test_open_archive_rejects_a_blank_path(path):
    with pytest.raises(CorruptedBackupError) as caught:
        with open_archive(path):
            pass
    assert "non-empty" in str(caught.value)


def test_open_archive_rejects_a_missing_file(tmp_path):
    with pytest.raises(CorruptedBackupError) as caught:
        with open_archive(str(tmp_path / "nope.zip")):
            pass
    assert "not found" in str(caught.value)


def test_open_archive_rejects_a_directory(tmp_path):
    with pytest.raises(CorruptedBackupError):
        with open_archive(str(tmp_path)):
            pass


def test_open_archive_rejects_a_non_zip_file(tmp_path):
    path = tmp_path / "not-a-zip.zip"
    path.write_text("this is definitely not a zip", encoding="utf-8")
    with pytest.raises(CorruptedBackupError) as caught:
        with open_archive(str(path)):
            pass
    assert caught.value.code == "STORAGE_BACKUP_CORRUPTED"


def test_open_archive_rejects_a_path_traversal_entry(tmp_path):
    path = _write_zip(str(tmp_path / "traversal.zip"), {"../evil.txt": b"x"})
    with pytest.raises(UnsafeArchivePathError) as caught:
        with open_archive(path):
            pass
    assert caught.value.detail == {"entry": "../evil.txt"}


def test_open_archive_rejects_a_symbolic_link_entry(tmp_path):
    path = _write_symlink_zip(str(tmp_path / "symlink.zip"))
    with pytest.raises(UnsafeArchivePathError) as caught:
        with open_archive(path):
            pass
    assert "symbolic link" in str(caught.value)


def test_open_archive_rejects_duplicate_entries(tmp_path):
    path = _write_raw_zip(str(tmp_path / "duplicate.zip"), [("a.txt", b"1"), ("a.txt", b"2")])
    with pytest.raises(UnsafeArchivePathError) as caught:
        with open_archive(path):
            pass
    assert "duplicate" in str(caught.value)


def test_open_archive_rejects_a_crc_mismatch(tmp_path):
    path = _write_zip(str(tmp_path / "broken.zip"), {"a.bin": _PAYLOAD}, stored=True)
    _corrupt_stored_payload(path)
    with pytest.raises(CorruptedBackupError) as caught:
        with open_archive(path):
            pass
    assert caught.value.detail == {"entry": "a.bin"}


def test_open_archive_can_skip_the_crc_check(tmp_path):
    """列表场景要能看到"有哪些备份", 不该因为 CRC 坏就整条不见。"""
    path = _write_zip(str(tmp_path / "broken.zip"), {"a.bin": _PAYLOAD}, stored=True)
    _corrupt_stored_payload(path)
    with open_archive(path, check_integrity=False) as archive:
        assert archive.namelist() == ["a.bin"]


def test_open_archive_reports_an_unsafe_name_before_a_crc_failure(tmp_path):
    """便宜的检查在前 —— 明显恶意/损坏的归档快速失败。"""
    path = _write_zip(str(tmp_path / "both.zip"), {"../evil.txt": _PAYLOAD}, stored=True)
    _corrupt_stored_payload(path)
    with pytest.raises(UnsafeArchivePathError):
        with open_archive(path):
            pass


# ======================================================================
# verify_zip_integrity
# ======================================================================


def test_verify_zip_integrity_accepts_a_healthy_archive(tmp_path):
    path = _write_zip(str(tmp_path / "ok.zip"), {"a.txt": b"hi"})
    with zipfile.ZipFile(path) as archive:
        verify_zip_integrity(archive, path)


def test_verify_zip_integrity_rejects_a_broken_entry(tmp_path):
    path = _write_zip(str(tmp_path / "broken.zip"), {"a.bin": _PAYLOAD}, stored=True)
    _corrupt_stored_payload(path)
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(CorruptedBackupError) as caught:
            verify_zip_integrity(archive, path)
    assert "CRC" in str(caught.value)


# ======================================================================
# read_entry_bytes / read_manifest_bytes
# ======================================================================


def test_read_entry_bytes_returns_the_payload(tmp_path):
    path = _write_zip(str(tmp_path / "ok.zip"), {"a.txt": "ñandú".encode("utf-8")})
    with zipfile.ZipFile(path) as archive:
        assert read_entry_bytes(archive, "a.txt") == "ñandú".encode("utf-8")


def test_read_entry_bytes_raises_when_required_and_missing(tmp_path):
    path = _write_zip(str(tmp_path / "ok.zip"), {"a.txt": b"hi"})
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(MissingArchiveEntryError) as caught:
            read_entry_bytes(archive, "nope.txt")
    assert caught.value.detail == {"entry": "nope.txt"}


def test_read_entry_bytes_returns_none_when_optional_and_missing(tmp_path):
    path = _write_zip(str(tmp_path / "ok.zip"), {"a.txt": b"hi"})
    with zipfile.ZipFile(path) as archive:
        assert read_entry_bytes(archive, "nope.txt", required=False) is None


def test_read_manifest_bytes_returns_the_manifest_payload(tmp_path):
    path = _write_zip(str(tmp_path / "ok.zip"), {MANIFEST_ENTRY_NAME: b'{"a":1}'})
    with zipfile.ZipFile(path) as archive:
        assert read_manifest_bytes(archive) == b'{"a":1}'


def test_read_manifest_bytes_raises_when_the_manifest_is_missing(tmp_path):
    path = _write_zip(str(tmp_path / "ok.zip"), {"database.sqlite": b"x"})
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(MissingArchiveEntryError) as caught:
            read_manifest_bytes(archive)
    assert caught.value.code == "STORAGE_BACKUP_MISSING_ENTRY"


# ======================================================================
# extract_to
# ======================================================================


def test_extract_to_writes_every_entry(tmp_path):
    path = _write_zip(
        str(tmp_path / "ok.zip"),
        {"database.sqlite": b"db", "materials/c1.json": b"{}", "audio/a.mp3": b"audio"},
    )
    destination = str(tmp_path / "out")
    with zipfile.ZipFile(path) as archive:
        written = extract_to(archive, destination)
    assert written == [
        os.path.join(destination, "audio", "a.mp3"),
        os.path.join(destination, "database.sqlite"),
        os.path.join(destination, "materials", "c1.json"),
    ]
    assert pathlib.Path(destination, "materials", "c1.json").read_bytes() == b"{}"


def test_extract_to_creates_nested_directories(tmp_path):
    path = _write_zip(str(tmp_path / "ok.zip"), {"a/b/c/d.txt": b"deep"})
    destination = str(tmp_path / "out")
    with zipfile.ZipFile(path) as archive:
        extract_to(archive, destination)
    assert pathlib.Path(destination, "a", "b", "c", "d.txt").read_bytes() == b"deep"


def test_extract_to_strips_the_prefix(tmp_path):
    """把归档里的 ``materials/audio/x`` 还原成 ``audio/x``。"""
    path = _write_zip(
        str(tmp_path / "ok.zip"),
        {"materials/audio/x.mp3": b"a", "materials/note.txt": b"n"},
    )
    destination = str(tmp_path / "out")
    with zipfile.ZipFile(path) as archive:
        written = extract_to(archive, destination, prefix="materials/")
    assert written == [
        os.path.join(destination, "audio", "x.mp3"),
        os.path.join(destination, "note.txt"),
    ]


def test_extract_to_with_a_prefix_writes_nothing_when_nothing_matches(tmp_path):
    path = _write_zip(str(tmp_path / "ok.zip"), {"audio/x.mp3": b"a"})
    destination = str(tmp_path / "out")
    with zipfile.ZipFile(path) as archive:
        assert extract_to(archive, destination, prefix="images/") == []


def test_extract_to_honours_an_explicit_entry_subset(tmp_path):
    path = _write_zip(
        str(tmp_path / "ok.zip"),
        {"database.sqlite": b"db", "audio/a.mp3": b"a"},
    )
    destination = str(tmp_path / "out")
    with zipfile.ZipFile(path) as archive:
        written = extract_to(archive, destination, entries=["database.sqlite"])
    assert written == [os.path.join(destination, "database.sqlite")]
    assert not os.path.exists(os.path.join(destination, "audio"))


def test_extract_to_with_an_empty_entry_list_writes_nothing(tmp_path):
    path = _write_zip(str(tmp_path / "ok.zip"), {"a.txt": b"a"})
    destination = str(tmp_path / "out")
    with zipfile.ZipFile(path) as archive:
        assert extract_to(archive, destination, entries=[]) == []


def test_extract_to_creates_the_destination(tmp_path):
    path = _write_zip(str(tmp_path / "ok.zip"), {"a.txt": b"a"})
    destination = str(tmp_path / "nested" / "deeper" / "out")
    with zipfile.ZipFile(path) as archive:
        extract_to(archive, destination)
    assert os.path.isdir(destination)


@pytest.mark.parametrize("escape", ["../escape.txt", "/absolute.txt", "C:/absolute.txt"])
def test_extract_to_refuses_to_escape_the_destination(tmp_path, escape):
    """**二次防线**: 即使条目名校验被绕过, 拼接结果也必须落在目标目录内。"""
    path = _write_zip(str(tmp_path / "ok.zip"), {"a.txt": b"a"})
    destination = str(tmp_path / "out")
    with zipfile.ZipFile(path) as archive:
        with pytest.raises(UnsafeArchivePathError) as caught:
            extract_to(archive, destination, entries=[escape])
    assert caught.value.detail == {"entry": escape}
    assert not os.path.exists(str(tmp_path / "escape.txt"))


def test_extract_to_overwrites_existing_files(tmp_path):
    """恢复是"忠实还原到那个时间点", 所以覆盖是预期行为。"""
    path = _write_zip(str(tmp_path / "ok.zip"), {"a.txt": b"new"})
    destination = str(tmp_path / "out")
    os.makedirs(destination, exist_ok=True)
    pathlib.Path(destination, "a.txt").write_bytes(b"old")
    with zipfile.ZipFile(path) as archive:
        extract_to(archive, destination)
    assert pathlib.Path(destination, "a.txt").read_bytes() == b"new"


def test_extract_to_restores_multilingual_names_and_content(tmp_path):
    name = "documents/apuntes-álgebra.txt"
    payload = "函数是一种关系。".encode("utf-8")
    path = _write_zip(str(tmp_path / "ok.zip"), {name: payload})
    destination = str(tmp_path / "out")
    with zipfile.ZipFile(path) as archive:
        extract_to(archive, destination)
    restored = pathlib.Path(destination, "documents", "apuntes-álgebra.txt")
    assert restored.read_bytes() == payload
