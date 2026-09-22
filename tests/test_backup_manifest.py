# -*- coding: utf-8 -*-
"""Task 43 —— 备份清单 (manifest.json) 与结构化错误码测试。

规范要求 manifest 至少包含::

    backup_version / schema_version / created_at / application_version
    file_count / database_checksum

本文件盯三件事:

1. **必需字段一个都不能少**, 且类型错误必须被拒绝 (清单是不可信输入);
2. ``file_count`` / ``total_bytes`` 由 ``build_manifest`` **算出来**,
   所以"数字与实际内容不一致"在构造期就不可能发生;
3. 备份层的每个错误码都能被 ``map_application_error`` 无胶水地映射到
   规范要求的 8 个用户可见错误码之一。

第 3 点值得单独说: 映射函数**先**判断 ``startswith("INVALID")``, 再判断
``endswith("NOT_FOUND")``。所以 ``INVALID_..._NOT_FOUND`` 这种两头都沾的
名字会被静默归到 INVALID_INPUT —— 有一条测试专门盯着这个陷阱。
"""

from __future__ import annotations

import hashlib
import json

import pytest

from src.application.errors import ERROR_CODES, map_application_error
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

#: 一个合法的 sha256 (内容随意, 只要求 64 位小写十六进制)。
_DB_CHECKSUM = hashlib.sha256(b"database").hexdigest()

#: 一个合法的材料文件摘要。
_MATERIAL_CHECKSUM = hashlib.sha256(b"material").hexdigest()


# ======================================================================
# 构造辅助
# ======================================================================


def _manifest_dict(**overrides) -> dict:
    """一份**合法**的清单字典; 用 ``overrides`` 精确破坏某一个字段。"""
    data = {
        "backup_version": BACKUP_VERSION,
        "schema_version": 2,
        "created_at": "2026-09-15T18:00:00+00:00",
        "application_version": "1.0.0",
        "file_count": 1,
        "database_checksum": _DB_CHECKSUM,
    }
    data.update(overrides)
    return data


def _material_file(path="audio/clase1.mp3", size=1024, digest=_MATERIAL_CHECKSUM) -> ManifestFile:
    return ManifestFile(path=path, size=size, sha256=digest)


def _built_manifest(**overrides) -> BackupManifest:
    options = {
        "schema_version": 2,
        "created_at": "2026-09-15T18:00:00+00:00",
        "application_version": "1.0.0",
        "database_checksum": _DB_CHECKSUM,
        "database_size": 4096,
        "database_filename": "classroom.sqlite",
    }
    options.update(overrides)
    return build_manifest(**options)


# ======================================================================
# 常量与必需字段
# ======================================================================


def test_required_manifest_fields_match_the_specification():
    """规范点名要求的 6 个字段, 一个不多一个不少。"""
    assert set(REQUIRED_MANIFEST_FIELDS) == {
        "backup_version",
        "schema_version",
        "created_at",
        "application_version",
        "file_count",
        "database_checksum",
    }


def test_backup_version_constants_are_consistent():
    assert BACKUP_VERSION >= 1
    assert MAX_SUPPORTED_BACKUP_VERSION >= BACKUP_VERSION


def test_archive_entry_names_are_fixed():
    """条目名固定, 归档才自描述 (不读清单也知道哪个是数据库)。"""
    assert MANIFEST_ENTRY_NAME == "manifest.json"
    assert DATABASE_ENTRY_NAME == "database.sqlite"


# ======================================================================
# from_dict: 必需字段
# ======================================================================


def test_from_dict_accepts_a_minimal_valid_manifest():
    manifest = BackupManifest.from_dict(_manifest_dict())
    assert manifest.backup_version == BACKUP_VERSION
    assert manifest.schema_version == 2
    assert manifest.file_count == 1
    assert manifest.database_checksum == _DB_CHECKSUM


def test_from_dict_rejects_a_non_mapping():
    with pytest.raises(ManifestError) as caught:
        BackupManifest.from_dict(["not", "an", "object"])
    assert caught.value.code == BackupErrorCode.STORAGE_BACKUP_MANIFEST_INVALID.value


@pytest.mark.parametrize("field", REQUIRED_MANIFEST_FIELDS)
def test_from_dict_rejects_a_missing_required_field(field):
    data = _manifest_dict()
    del data[field]
    with pytest.raises(ManifestError) as caught:
        BackupManifest.from_dict(data)
    assert field in str(caught.value)


def test_from_dict_reports_every_missing_required_field_at_once():
    """一次列全, 而不是让调用方逐个试 —— 少字段时最烦人的体验。"""
    with pytest.raises(ManifestError) as caught:
        BackupManifest.from_dict({})
    message = str(caught.value)
    for field in REQUIRED_MANIFEST_FIELDS:
        assert field in message


# ======================================================================
# from_dict: 版本
# ======================================================================


def test_from_dict_accepts_the_max_supported_version():
    manifest = BackupManifest.from_dict(
        _manifest_dict(backup_version=MAX_SUPPORTED_BACKUP_VERSION)
    )
    assert manifest.backup_version == MAX_SUPPORTED_BACKUP_VERSION


def test_from_dict_rejects_a_newer_backup_version():
    """更高版本绝不猜着读。"""
    with pytest.raises(UnsupportedBackupVersionError) as caught:
        BackupManifest.from_dict(_manifest_dict(backup_version=MAX_SUPPORTED_BACKUP_VERSION + 1))
    assert caught.value.code == BackupErrorCode.INVALID_BACKUP_VERSION.value


def test_from_dict_rejects_a_zero_backup_version():
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(backup_version=0))


def test_from_dict_rejects_a_boolean_backup_version():
    """``True`` 是 ``int`` 的子类 —— 必须单独挡掉, 否则 ``True`` 会变成版本 1。"""
    with pytest.raises(ManifestError) as caught:
        BackupManifest.from_dict(_manifest_dict(backup_version=True))
    assert "integer" in str(caught.value)


def test_from_dict_rejects_a_string_backup_version():
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(backup_version="1"))


def test_from_dict_accepts_schema_version_zero():
    """schema 0 = "还没有台账的库", 是合法状态 (而不是错误)。"""
    manifest = BackupManifest.from_dict(_manifest_dict(schema_version=0))
    assert manifest.schema_version == 0


def test_from_dict_rejects_a_negative_schema_version():
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(schema_version=-1))


def test_from_dict_rejects_a_boolean_schema_version():
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(schema_version=True))


# ======================================================================
# from_dict: 字符串与计数
# ======================================================================


@pytest.mark.parametrize("field", ["created_at", "application_version"])
def test_from_dict_rejects_a_blank_string_field(field):
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(**{field: "   "}))


@pytest.mark.parametrize("field", ["created_at", "application_version"])
def test_from_dict_rejects_a_non_string_field(field):
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(**{field: 20260915}))


def test_from_dict_rejects_a_negative_file_count():
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(file_count=-1))


def test_from_dict_accepts_a_zero_file_count():
    """``file_count=0`` 类型上合法 (虽然 build_manifest 不会产出它)。"""
    manifest = BackupManifest.from_dict(_manifest_dict(file_count=0))
    assert manifest.file_count == 0


# ======================================================================
# from_dict: 数据库校验和
# ======================================================================


def test_from_dict_rejects_a_short_database_checksum():
    with pytest.raises(ManifestError) as caught:
        BackupManifest.from_dict(_manifest_dict(database_checksum=_DB_CHECKSUM[:32]))
    assert "64" in str(caught.value)


def test_from_dict_rejects_an_uppercase_database_checksum():
    """大小写不统一会让"逐字节比较"悄悄失效。"""
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(database_checksum=_DB_CHECKSUM.upper()))


def test_from_dict_rejects_a_non_hex_database_checksum():
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(database_checksum="z" * 64))


def test_from_dict_rejects_a_non_string_database_checksum():
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(database_checksum=64))


# ======================================================================
# from_dict: 材料文件记录
# ======================================================================


def test_from_dict_accepts_material_files():
    manifest = BackupManifest.from_dict(
        _manifest_dict(
            file_count=2,
            material_files=[_material_file().to_dict()],
        )
    )
    assert manifest.material_file_count == 1
    assert manifest.material_files[0].path == "audio/clase1.mp3"


def test_from_dict_treats_a_missing_material_files_list_as_empty():
    manifest = BackupManifest.from_dict(_manifest_dict())
    assert manifest.material_files == ()


def test_from_dict_rejects_material_files_that_are_not_a_list():
    with pytest.raises(ManifestError) as caught:
        BackupManifest.from_dict(_manifest_dict(material_files={"a": 1}))
    assert "list" in str(caught.value)


def test_from_dict_rejects_a_material_entry_that_is_not_an_object():
    with pytest.raises(ManifestError) as caught:
        BackupManifest.from_dict(_manifest_dict(material_files=["audio/x.mp3"]))
    assert "material_files[0]" in str(caught.value)


@pytest.mark.parametrize("bad_path", [None, "", 42])
def test_from_dict_rejects_a_material_entry_without_a_path(bad_path):
    entry = _material_file().to_dict()
    entry["path"] = bad_path
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(material_files=[entry]))


@pytest.mark.parametrize("bad_digest", [None, "", "abc", _MATERIAL_CHECKSUM[:63], 5])
def test_from_dict_rejects_a_material_entry_with_a_bad_digest(bad_digest):
    entry = _material_file().to_dict()
    entry["sha256"] = bad_digest
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(material_files=[entry]))


@pytest.mark.parametrize("bad_size", [-1, "1024", None, True])
def test_from_dict_rejects_a_material_entry_with_a_bad_size(bad_size):
    entry = _material_file().to_dict()
    entry["size"] = bad_size
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(material_files=[entry]))


def test_from_dict_accepts_a_zero_sized_material_file():
    """空文件是合法的课堂材料 (例如老师发了一个空模板)。"""
    entry = _material_file(size=0).to_dict()
    manifest = BackupManifest.from_dict(_manifest_dict(file_count=2, material_files=[entry]))
    assert manifest.material_files[0].size == 0


# ======================================================================
# from_dict: 增强字段
# ======================================================================


def test_from_dict_rejects_a_non_string_label():
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(label=2026))


def test_from_dict_accepts_a_null_label():
    assert BackupManifest.from_dict(_manifest_dict(label=None)).label is None


def test_from_dict_rejects_a_non_mapping_config():
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(_manifest_dict(config=["data_dir"]))


def test_from_dict_defaults_the_enhanced_fields():
    manifest = BackupManifest.from_dict(_manifest_dict())
    assert manifest.database_size == 0
    assert manifest.total_bytes == 0
    assert manifest.database_filename == ""
    assert manifest.label is None
    assert manifest.material_files == ()
    assert manifest.config == {}


@pytest.mark.parametrize("bad_value", [-1, "4096", None, True])
def test_from_dict_coerces_an_invalid_optional_int_to_zero(bad_value):
    """增强字段坏掉不该让整份清单报废 —— 它们是提示性的, 不是判据。"""
    manifest = BackupManifest.from_dict(
        _manifest_dict(database_size=bad_value, total_bytes=bad_value)
    )
    assert manifest.database_size == 0
    assert manifest.total_bytes == 0


def test_from_dict_reads_the_database_filename():
    manifest = BackupManifest.from_dict(_manifest_dict(database_filename="classroom.sqlite"))
    assert manifest.database_filename == "classroom.sqlite"


# ======================================================================
# build_manifest: 数字由构造方算出来
# ======================================================================


def test_build_manifest_counts_the_database_itself():
    """``file_count`` 含数据库条目 —— 归档里除了清单就是数据库 + 材料。"""
    manifest = _built_manifest()
    assert manifest.file_count == 1
    assert manifest.material_file_count == 0


def test_build_manifest_derives_file_count_from_the_material_files():
    manifest = _built_manifest(
        material_files=[_material_file("audio/a.mp3"), _material_file("documents/b.pdf")]
    )
    assert manifest.file_count == 3
    assert manifest.material_file_count == 2


def test_build_manifest_derives_total_bytes_from_the_database_and_materials():
    manifest = _built_manifest(
        database_size=1000,
        material_files=[_material_file("audio/a.mp3", size=250), _material_file("audio/b.mp3", size=50)],
    )
    assert manifest.total_bytes == 1300


def test_build_manifest_sets_the_current_backup_version():
    assert _built_manifest().backup_version == BACKUP_VERSION


def test_build_manifest_keeps_the_label():
    assert _built_manifest(label="before-exam").label == "before-exam"


def test_build_manifest_keeps_the_config():
    manifest = _built_manifest(config={"backed_up_dirs": ["audio"]})
    assert manifest.config == {"backed_up_dirs": ["audio"]}


def test_build_manifest_output_is_self_consistent():
    """构造期就自洽 —— 所以"数字与实际内容不一致"不可能被写出去。"""
    manifest = _built_manifest(material_files=[_material_file()])
    manifest.validate_self_consistency()


def test_build_manifest_rejects_a_non_sequence_material_files():
    with pytest.raises(TypeError):
        _built_manifest(material_files=123)


# ======================================================================
# validate_self_consistency
# ======================================================================


def test_validate_self_consistency_rejects_a_file_count_mismatch():
    """手写清单可以撒谎, 所以读取后还要再自洽一次。"""
    manifest = BackupManifest(
        backup_version=BACKUP_VERSION,
        schema_version=2,
        created_at="2026-09-15T18:00:00+00:00",
        application_version="1.0.0",
        file_count=7,
        database_checksum=_DB_CHECKSUM,
        material_files=(_material_file(),),
    )
    with pytest.raises(ManifestError) as caught:
        manifest.validate_self_consistency()
    assert "file_count" in str(caught.value)


def test_validate_self_consistency_rejects_zero_total_bytes_with_materials():
    manifest = BackupManifest(
        backup_version=BACKUP_VERSION,
        schema_version=2,
        created_at="2026-09-15T18:00:00+00:00",
        application_version="1.0.0",
        file_count=2,
        database_checksum=_DB_CHECKSUM,
        material_files=(_material_file(),),
        total_bytes=0,
    )
    with pytest.raises(ManifestError) as caught:
        manifest.validate_self_consistency()
    assert "total_bytes" in str(caught.value)


def test_validate_self_consistency_allows_zero_total_bytes_without_materials():
    """空库 + 无材料 -> 总字节数真的是 0, 这不是矛盾。"""
    manifest = _built_manifest(database_size=0)
    manifest.validate_self_consistency()


# ======================================================================
# 序列化
# ======================================================================


def test_manifest_file_to_dict_round_trip():
    original = _material_file()
    assert ManifestFile.from_dict(original.to_dict()) == original


def test_manifest_to_dict_round_trip_is_lossless():
    original = _built_manifest(
        label="before-exam",
        material_files=[_material_file("documents/apuntes-álgebra.txt", size=17)],
        config={"backed_up_dirs": ["materials", "audio"]},
    )
    assert BackupManifest.from_dict(original.to_dict()) == original


def test_manifest_bytes_are_canonical_json():
    """键排序 + 紧凑分隔符 + 不转义非 ASCII (与持久化层同一套规则)。"""
    manifest = _built_manifest(label="Càlcul")
    text = manifest_to_bytes(manifest).decode("utf-8")
    assert text == json.dumps(
        json.loads(text), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    assert "Càlcul" in text  # 加泰语原样保留, 不是 \uXXXX
    assert ", " not in text and ": " not in text


def test_manifest_bytes_are_deterministic():
    manifest = _built_manifest(material_files=[_material_file()])
    assert manifest_to_bytes(manifest) == manifest_to_bytes(manifest)


def test_manifest_bytes_round_trip():
    original = _built_manifest(material_files=[_material_file()])
    assert manifest_from_bytes(manifest_to_bytes(original)) == original


def test_manifest_from_bytes_accepts_bytearray_and_memoryview():
    payload = manifest_to_bytes(_built_manifest())
    assert manifest_from_bytes(bytearray(payload)) == _built_manifest()
    assert manifest_from_bytes(memoryview(payload)) == _built_manifest()


@pytest.mark.parametrize("bad_payload", ["{}", 123, None])
def test_manifest_from_bytes_rejects_non_bytes(bad_payload):
    with pytest.raises(ManifestError) as caught:
        manifest_from_bytes(bad_payload)
    assert "bytes" in str(caught.value)


def test_manifest_from_bytes_rejects_invalid_utf8():
    with pytest.raises(ManifestError) as caught:
        manifest_from_bytes(b"\xff\xfe\x00\x01")
    assert "UTF-8" in str(caught.value)


def test_manifest_from_bytes_rejects_invalid_json():
    with pytest.raises(ManifestError) as caught:
        manifest_from_bytes(b"{not json")
    assert "JSON" in str(caught.value)


def test_manifest_from_bytes_rejects_a_json_array():
    with pytest.raises(ManifestError) as caught:
        manifest_from_bytes(b"[1,2,3]")
    assert "object" in str(caught.value)


def test_manifest_from_bytes_rejects_a_json_string():
    with pytest.raises(ManifestError):
        manifest_from_bytes(b'"manifest"')


# ======================================================================
# 错误码 -> 用户可见错误码 (无胶水映射)
# ======================================================================

#: 每个备份错误码**应当**映射到的用户可见错误码。
EXPECTED_MAPPING: dict[BackupErrorCode, str] = {
    BackupErrorCode.INVALID_INPUT: ERROR_CODES.INVALID_INPUT.value,
    BackupErrorCode.INVALID_BACKUP_VERSION: ERROR_CODES.INVALID_INPUT.value,
    BackupErrorCode.BACKUP_NOT_FOUND: ERROR_CODES.NOT_FOUND.value,
    BackupErrorCode.DUPLICATE_BACKUP: ERROR_CODES.CONFLICT.value,
    BackupErrorCode.STORAGE_ERROR: ERROR_CODES.STORAGE_ERROR.value,
    BackupErrorCode.STORAGE_BACKUP_CORRUPTED: ERROR_CODES.STORAGE_ERROR.value,
    BackupErrorCode.STORAGE_BACKUP_MANIFEST_INVALID: ERROR_CODES.STORAGE_ERROR.value,
    BackupErrorCode.STORAGE_BACKUP_CHECKSUM_MISMATCH: ERROR_CODES.STORAGE_ERROR.value,
    BackupErrorCode.STORAGE_BACKUP_UNSAFE_PATH: ERROR_CODES.STORAGE_ERROR.value,
    BackupErrorCode.STORAGE_BACKUP_TOO_LARGE: ERROR_CODES.STORAGE_ERROR.value,
    BackupErrorCode.STORAGE_BACKUP_MISSING_ENTRY: ERROR_CODES.STORAGE_ERROR.value,
    BackupErrorCode.STORAGE_DATABASE_IN_USE: ERROR_CODES.STORAGE_ERROR.value,
    BackupErrorCode.STORAGE_RESTORE_FAILED: ERROR_CODES.STORAGE_ERROR.value,
}


def test_the_mapping_table_covers_every_backup_error_code():
    """漏一个码就等于漏一条用户可见的错误路径。"""
    assert set(EXPECTED_MAPPING) == set(BackupErrorCode)


@pytest.mark.parametrize("code", sorted(EXPECTED_MAPPING, key=lambda c: c.value))
def test_every_backup_error_code_maps_to_the_expected_http_code(code):
    mapped = map_application_error(BackupError("boom", code=code))
    assert mapped.code == EXPECTED_MAPPING[code]


@pytest.mark.parametrize("code", sorted(BackupErrorCode, key=lambda c: c.value))
def test_no_backup_error_code_is_ambiguous_between_invalid_and_not_found(code):
    """``map_application_error`` 先看 ``INVALID`` 前缀再看 ``NOT_FOUND`` 后缀。

    一个 ``INVALID_..._NOT_FOUND`` 的码会静默变成 INVALID_INPUT ——
    这不是我们想要的语义, 所以这种名字根本不该存在。
    """
    assert not (code.value.startswith("INVALID") and code.value.endswith("NOT_FOUND"))


#: 具体异常类 -> 它声明的错误码。
EXPECTED_CLASS_CODES: dict[type, BackupErrorCode] = {
    BackupValidationError: BackupErrorCode.INVALID_INPUT,
    UnsupportedBackupVersionError: BackupErrorCode.INVALID_BACKUP_VERSION,
    BackupNotFoundError: BackupErrorCode.BACKUP_NOT_FOUND,
    DuplicateBackupError: BackupErrorCode.DUPLICATE_BACKUP,
    CorruptedBackupError: BackupErrorCode.STORAGE_BACKUP_CORRUPTED,
    ManifestError: BackupErrorCode.STORAGE_BACKUP_MANIFEST_INVALID,
    ChecksumMismatchError: BackupErrorCode.STORAGE_BACKUP_CHECKSUM_MISMATCH,
    UnsafeArchivePathError: BackupErrorCode.STORAGE_BACKUP_UNSAFE_PATH,
    ArchiveTooLargeError: BackupErrorCode.STORAGE_BACKUP_TOO_LARGE,
    MissingArchiveEntryError: BackupErrorCode.STORAGE_BACKUP_MISSING_ENTRY,
    DatabaseInUseError: BackupErrorCode.STORAGE_DATABASE_IN_USE,
    RestoreError: BackupErrorCode.STORAGE_RESTORE_FAILED,
}


@pytest.mark.parametrize("error_class", sorted(EXPECTED_CLASS_CODES, key=lambda c: c.__name__))
def test_concrete_backup_errors_carry_their_documented_code(error_class):
    error = error_class("boom")
    assert error.code == EXPECTED_CLASS_CODES[error_class].value
    assert error.error_code == error.code


def test_every_concrete_backup_error_is_a_backup_error():
    for error_class in EXPECTED_CLASS_CODES:
        assert issubclass(error_class, BackupError)


def test_backup_error_to_dict_exposes_code_and_message():
    payload = BackupValidationError("label is empty").to_dict()
    assert payload == {
        "code": BackupErrorCode.INVALID_INPUT.value,
        "message": "label is empty",
    }


def test_backup_error_omits_an_empty_detail_from_to_dict():
    assert "detail" not in BackupValidationError("x").to_dict()
    assert "detail" not in BackupValidationError("x", detail={}).to_dict()


def test_backup_error_includes_a_non_empty_detail():
    payload = UnsafeArchivePathError("bad", detail={"entry": "../x"}).to_dict()
    assert payload["detail"] == {"entry": "../x"}


def test_backup_error_detail_is_copied_not_aliased():
    """错误对象可能被长期持有; 外部字典事后被改不该影响它。"""
    source = {"entry": "a"}
    error = UnsafeArchivePathError("bad", detail=source)
    source["entry"] = "b"
    assert error.detail == {"entry": "a"}


def test_backup_error_accepts_an_explicit_code_override():
    error = BackupError("boom", code=BackupErrorCode.STORAGE_BACKUP_CORRUPTED)
    assert error.code == BackupErrorCode.STORAGE_BACKUP_CORRUPTED.value


def test_backup_error_accepts_a_plain_string_code():
    assert BackupError("boom", code="STORAGE_CUSTOM").code == "STORAGE_CUSTOM"


def test_backup_error_keeps_its_cause():
    cause = ValueError("inner")
    error = RestoreError("outer", cause=cause)
    assert error.cause is cause


def test_backup_error_str_contains_the_code():
    assert "[DUPLICATE_BACKUP]" in str(DuplicateBackupError("already exists"))


def test_backup_error_maps_through_map_application_error_without_a_domain_code():
    """没有 ``code`` 属性的普通异常走既有规则 (ValueError -> INVALID_INPUT)。"""
    assert map_application_error(ValueError("bad")).code == ERROR_CODES.INVALID_INPUT.value
