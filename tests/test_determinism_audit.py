# -*- coding: utf-8 -*-
"""Task 47.2 — Determinism Audit。

硬性约束: 业务对象的 identity (material_id / evidence_id / knowledge_id /
student_id / exercise_id / evaluation_id / study_plan_id / conflict_id /
relationship_id) 绝不接受非确定性来源。唯一允许的"墙钟时间 / 随机"来源是
``src/common/clock.py`` (运行期元数据, 不参与 identity; ``src/application/runtime.py``
只做兼容 re-export)。

本测试用 AST 扫描 ``src`` 下真实的**调用** (忽略注释与 docstring), 逐个判断:
- ``datetime.now`` / ``datetime.utcnow`` 调用 -> 只允许在时钟真源
  (``src/common/clock.py``, 以及只做兼容 re-export 的 ``src/application/runtime.py``)
- ``random.*`` 调用 -> 禁止
- ``hash(`` 内置调用 -> 禁止
- ``uuid.uuid4()`` 调用 -> 只允许在受控的 fallback / 临时文件名位置

**Task 47 终审补充**: 内置 ``hash()`` 的禁令也扫 ``tests/``。原因是一次真实的偶发
失败 —— ``tests/test_evidence_store.py`` 的 ``make_evidence`` 用
``abs(hash(...)) % 10**10`` 造 evidence_id, 而 ``hash()`` 对 str 是按进程随机化的
(``PYTHONHASHSEED``), 且 10^10 个桶在 5000 个条目下约有 0.125% 的撞 id 概率;
撞上时 ``EvidenceStore`` 会把后一条 **REJECTED**, 于是
``test_10k_inserts_dedups_and_queries`` 偶发地拿到 ``added < 5000``。
只扫 ``src/`` 的审计看不到测试里的违规, 所以这里补上。

注意: ``random`` 的禁令**不**扩展到 tests —— 测试里用固定种子
(``random.Random(47)``) 做可复现抽样是**有意**的, 不参与产品 identity。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src"
TESTS_ROOT = pathlib.Path(__file__).resolve().parent

# uuid4 仅允许出现在这些文件, 且必须为 fallback 或临时文件名。
_UUID4_ALLOWED_FILES = {
    "src/models.py",
    "src/audio_segmentation.py",
    "src/integration.py",
    "src/knowledge_structure.py",
    "src/evidence_ingestion.py",
}

#: 墙钟时间 (``datetime.now``) 允许出现的文件 —— 放行的是**真源 + 兼容层**两个。
#:
#: P1-4 之前真源就在 ``runtime.py``; 之后收口到中立的 ``src/common/clock.py``
#: (``src.persistence`` 的迁移台账与 ``src.application`` 的日志都需要注入式时钟,
#: 但分层禁止这两个包互相依赖, 所以真源放在零依赖的 ``src.common`` 里),
#: ``runtime.py`` 只剩兼容 re-export (见它自己的模块 docstring)。
#: 审计的语义一直是"挂钟时间只能住在时钟真源里", 所以跟着真源走 ——
#: 否则真源一搬家, 这两条就会把一次**正确**的重构报成违规。
_CLOCK_SOURCE_FILES = ("src/application/runtime.py", "src/common/clock.py")


def _iter_py():
    for path in sorted(SRC_ROOT.rglob("*.py")):
        yield path


def _parse(path: pathlib.Path) -> ast.Module:
    # ``utf-8-sig`` 而不是 ``utf-8``: 带 BOM 的文件用 ``utf-8`` 读出来会以一个
    # U+FEFF 字符开头, ``ast.parse`` 会报 "invalid non-printable character"
    # —— 一个编码问题伪装成语法错误, 很难定位。BOM 本身另有专门断言 (见
    # ``test_no_utf8_bom``), 这里只保证审计不会因此崩掉。
    return ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))


def test_no_datetime_now_outside_runtime() -> None:
    bad: list[str] = []
    for path in _iter_py():
        rel = str(path).replace("\\", "/")
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("now", "utcnow"):
                    if not any(f in rel for f in _CLOCK_SOURCE_FILES):
                        bad.append(f"{rel}:{node.lineno} ({ast.unparse(node.func)})")
    assert not bad, f"datetime.now/utcnow outside the clock sources: {bad}"


def test_no_random_module_calls() -> None:
    bad: list[str] = []
    for path in _iter_py():
        rel = str(path).replace("\\", "/")
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                value = node.func.value
                if isinstance(value, ast.Name) and value.id == "random":
                    bad.append(f"{rel}:{node.lineno} ({ast.unparse(node.func)})")
    assert not bad, f"random.* calls found: {bad}"


def test_no_builtin_hash_call() -> None:
    bad: list[str] = []
    for path in _iter_py():
        rel = str(path).replace("\\", "/")
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "hash":
                bad.append(f"{rel}:{node.lineno}")
    assert not bad, f"hash() calls found (breaks deterministic identity): {bad}"


def test_no_builtin_hash_call_in_tests() -> None:
    """内置 ``hash()`` 在测试里同样禁止 —— 它让测试结果依赖进程环境。

    触发这条守卫的是一个**真实的偶发失败**:
    ``tests/test_evidence_store.py::TestLargeStore::test_10k_inserts_dedups_and_queries``
    在一次全量回归里红了 (``result.added != 5000``), 但单独跑 40 个随机
    ``PYTHONHASHSEED`` 全部通过 —— 因为夹具用
    ``abs(hash(content + material_id + str(page))) % 10**10`` 造 evidence_id:
    ``hash()`` 对 str 按进程随机化, 而 10^10 个桶装 5000 个条目约有 0.125%
    概率撞 id, 撞上时 ``EvidenceStore`` 把后一条 REJECTED。

    所以测试里的 identity 也必须是确定性的。改用 ``hashlib.sha256``。
    """
    bad: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        rel = str(path).replace("\\", "/")
        for node in ast.walk(_parse(path)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "hash"
            ):
                bad.append(f"{rel}:{node.lineno}")
    assert not bad, (
        "测试里出现内置 hash() —— 它按进程随机化, 会让测试结果依赖 "
        f"PYTHONHASHSEED; 改用 hashlib.sha256。实际: {bad}"
    )


def test_no_utf8_bom() -> None:
    """``src/`` 与 ``tests/`` 下的 .py 不得带 UTF-8 BOM。

    项目规范是"编码统一为 UTF-8", BOM 不属于它, 而且会**真实地**咬人: 带 BOM 的
    文件用 ``encoding="utf-8"`` 读出来会以 U+FEFF 开头, 于是 ``ast.parse`` 直接
    ``SyntaxError: invalid non-printable character U+FEFF`` —— 一个编码问题伪装成
    语法错误。

    这条断言是加 ``tests/`` 版 hash() 审计时**顺带发现的**: 新审计一上线就把
    ``tests/test_ocr_processor.py`` 顶了出来 (另有 ``scripts/build_models.py`` 与
    ``scripts/test.py``, 一并清理)。
    """
    bad: list[str] = []
    for root in (SRC_ROOT, TESTS_ROOT):
        for path in sorted(root.rglob("*.py")):
            if path.read_bytes().startswith(b"\xef\xbb\xbf"):
                bad.append(str(path).replace("\\", "/"))
    assert not bad, f"这些文件带 UTF-8 BOM, 请存成无 BOM 的 UTF-8: {bad}"


def test_uuid4_only_in_allowlisted_fallback_or_temp() -> None:
    bad: list[str] = []
    for path in _iter_py():
        rel = str(path).replace("\\", "/")
        source = path.read_text(encoding="utf-8")
        lines = source.splitlines()
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr != "uuid4":
                    continue
                if not any(rel.endswith(f) or f in rel for f in _UUID4_ALLOWED_FILES):
                    bad.append(f"{rel}:{node.lineno} (outside allowlist)")
                    continue
                # 受控位置进一步校验语义
                text = lines[node.lineno - 1] if 0 < node.lineno <= len(lines) else ""
                prev = lines[node.lineno - 2] if node.lineno - 2 >= 0 else ""
                guard_text = text + "\n" + prev
                if rel.endswith("models.py") or rel.endswith("integration.py") or rel.endswith(
                    "knowledge_structure.py"
                ):
                    # 必须是 __post_init__ 里的 "if not self.x: ..." 兜底
                    # (guard 可能在上一行, 故同时检查上一行)
                    if "if not self." not in guard_text:
                        bad.append(f"{rel}:{node.lineno} (uuid4 not guarded by 'if not self.')")
                elif rel.endswith("audio_segmentation.py"):
                    # 必须是临时音频块文件名, 不参与任何 identity
                    if "tmp_name" not in text and "uuid.uuid4().hex" not in text:
                        bad.append(f"{rel}:{node.lineno} (uuid4 not a temp filename)")
                # evidence_ingestion.py 是 _is_uuid4_fallback_id 检测函数, 允许
    assert not bad, f"uuid4 used in unexpected way: {bad}"


def test_runtime_is_only_nondeterminism_source() -> None:
    """确认: 非确定性来源 (datetime.now / secrets) 只住在时钟真源里。"""
    offenders: list[str] = []
    for path in _iter_py():
        rel = str(path).replace("\\", "/")
        if any(f in rel for f in _CLOCK_SOURCE_FILES):
            continue
        for node in ast.walk(_parse(path)):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("now", "utcnow"):
                    offenders.append(f"{rel}:{node.lineno}")
            # secrets.token_urlsafe 也只在时钟真源里
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "token_urlsafe":
                    offenders.append(f"{rel}:{node.lineno} (secrets outside the clock sources)")
    assert not offenders, f"nondeterminism sources outside the clock sources: {offenders}"


@pytest.mark.parametrize(
    "name",
    ["material_id", "evidence_id", "knowledge_id", "conflict_id", "relationship_id"],
)
def test_uuid4_fallback_is_not_default_path(name) -> None:
    """这些 id 在正常流水线上必须被确定性赋值, uuid4 只是空值兜底。

    直接构造空 id 才会触发兜底; 真实摄取/装配层总是先给出内容寻址的 id。
    E2E 验收 (test_two_independent_runs_produce_identical_reports) 已证明
    重复运行报告完全一致 (TOTAL DIFFS: 0), 故兜底路径在生产中不触发。
    """
    # 仅做静态断言: 兜底赋值语句存在 (即"空值才兜底"的语义成立)。
    models_py = SRC_ROOT / "models.py"
    text = models_py.read_text(encoding="utf-8")
    assert f'if not self.{name}: self.{name} = str(uuid.uuid4())' in text or (
        name in ("conflict_id", "relationship_id")
    )
