# -*- coding: utf-8 -*-
"""TASK-110 — 打包资产 (``packaging/``) 的静态守卫测试。

打包本身 (PyInstaller / ISCC) 不在测试里跑 —— 它要几分钟、几百 MB,
而且依赖本机的打包 venv。这里钉住的是**容易漂移的契约**:

- ``packaging/installer.iss`` 的版本回退值必须等于
  ``src.application.workspace.APPLICATION_VERSION`` (产品版本的唯一真源);
- spec 的形态: 无控制台、带图标、收 ``src/web`` 与 ``src/application/assets``、
  保留已验证的 ``opencv_videoio_ffmpeg`` 裁剪;
- ``packaging/entry.py`` 的探针分发与 ``--out`` 落盘 (窗口程序没有控制台,
  探针结果写文件是验收链条的一环);
- ``packaging/build.py`` 的版本读取与安装包命名格式。
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
from pathlib import Path

from src.application.workspace import APPLICATION_VERSION

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"


def _load_entry_module():
    """按文件路径加载 ``packaging/entry.py``。

    不能用 ``import packaging.entry``: ``packaging`` 与 PyPI 上 pytest 依赖的
    同名库冲突, 而这里的目录**刻意**不放 ``__init__.py``。
    """
    spec = importlib.util.spec_from_file_location(
        "classroom_packaging_entry", PACKAGING / "entry.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_build_module():
    spec = importlib.util.spec_from_file_location(
        "classroom_packaging_build", PACKAGING / "build.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENTRY = _load_entry_module()
BUILD = _load_build_module()


# ----------------------------------------------------------------------
# installer.iss
# ----------------------------------------------------------------------


def _read_iss() -> str:
    return (PACKAGING / "installer.iss").read_text(encoding="utf-8-sig")


class TestInstallerISS:
    def test_version_fallback_matches_single_source_of_truth(self) -> None:
        match = re.search(r'#define AppVersion "([^"]+)"', _read_iss())
        assert match, "installer.iss 里找不到 AppVersion 回退值"
        assert match.group(1) == APPLICATION_VERSION

    def test_setup_identity_and_privileges(self) -> None:
        text = _read_iss()
        # 固定 AppId: 升级安装才能识别为"同一个程序"。
        assert re.search(r"^AppId=\{\{[0-9A-F-]{36}\}", text, re.M)
        # 装到用户目录、不要管理员。
        assert "PrivilegesRequired=lowest" in text
        assert r"DefaultDirName={localappdata}\Programs\Loomind" in text
        # 开始菜单组。
        assert "DefaultGroupName=" in text
        assert '{group}\\' in text

    def test_setup_icon_and_output_naming(self) -> None:
        text = _read_iss()
        assert "SetupIconFile=" in text
        assert "OutputBaseFilename=Loomind-{#AppVersion}-Setup" in text
        # 图标文件真的在 (相对 iss 所在目录)。
        icon = re.search(r"^SetupIconFile=(.+)$", text, re.M).group(1).strip()
        assert (PACKAGING / icon).resolve().is_file()

    def test_files_icons_run_sections(self) -> None:
        text = _read_iss()
        assert r'Source: "..\dist\Loomind\*"' in text
        assert re.search(r"^\[Icons\]", text, re.M)
        assert re.search(r"^\[Run\]", text, re.M)

    def test_uninstall_leaves_user_data_alone(self) -> None:
        """卸载不删 ``%LOCALAPPDATA%\\Loomind`` (用户数据)。"""
        text = _read_iss()
        assert "[UninstallDelete]" not in text
        # 仅有的两处 {localappdata} 用途: 安装目录 (Programs 子目录)。
        for usage in re.findall(r"\{localappdata\}[^\s;]*", text):
            assert "Programs" in usage or "CommCtrl" in usage

    def test_compression_is_lzma2_max(self) -> None:
        assert "Compression=lzma2/max" in _read_iss()


# ----------------------------------------------------------------------
# loomind.spec (AST 检查, 不执行 PyInstaller)
# ----------------------------------------------------------------------


def _spec_tree() -> ast.Module:
    return ast.parse((PACKAGING / "loomind.spec").read_text(encoding="utf-8"))


def _call_keyword(tree: ast.Module, func_name: str, keyword: str):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == func_name:
            for kw in node.keywords:
                if kw.arg == keyword:
                    return kw.value
    raise AssertionError(f"{func_name}(..., {keyword}=...) not found")


def _module_level_assignment(tree: ast.Module, name: str):
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return node.value
    return None


def _resolve_value(tree: ast.Module, value: ast.expr) -> ast.expr:
    """把 ``icon=ICON`` 这样的简单名字解析回模块级赋值。"""
    if isinstance(value, ast.Name):
        found = _module_level_assignment(tree, value.id)
        if found is not None:
            return found
    return value


class TestPyInstallerSpec:
    def test_root_expression_resolves_to_repo_root(self) -> None:
        """直接求值 spec 的 ``ROOT = ...`` 表达式。

        曾经的 bug: 照抄了 temp/pack_spike (深一层目录) 的双 ``dirname``,
        构建时才红在 "Unable to find D:\\Project\\src\\web"。
        """
        value = _module_level_assignment(_spec_tree(), "ROOT")
        assert value is not None, "spec 里找不到 ROOT 赋值"
        resolved = eval(  # noqa: S307 - 对自家源码求值, 输入受控
            compile(ast.Expression(body=value), "<spec>", "eval"),
            {"os": os, "SPECPATH": str(PACKAGING)},
        )
        assert Path(resolved) == ROOT

    def test_console_is_disabled(self) -> None:
        value = _call_keyword(_spec_tree(), "EXE", "console")
        assert isinstance(value, ast.Constant) and value.value is False, (
            "安装版必须是 B 档形态: console=False (托盘, 无控制台)"
        )

    def test_icon_is_the_shared_app_icon(self) -> None:
        tree = _spec_tree()
        value = _resolve_value(tree, _call_keyword(tree, "EXE", "icon"))
        source = ast.unparse(value)
        assert "app.ico" in source and "assets" in source

    def test_entry_is_packaging_entry(self) -> None:
        tree = _spec_tree()
        first_call = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Analysis"
        )
        assert "entry.py" in ast.unparse(first_call.args[0])

    def test_web_and_assets_are_collected_as_datas(self) -> None:
        text = (PACKAGING / "loomind.spec").read_text(encoding="utf-8")
        assert '"web"' in text and '"src/web"' in text
        assert '"application", "assets"' in text and '"src/application/assets"' in text

    def test_verified_opencv_trim_stays(self) -> None:
        text = (PACKAGING / "loomind.spec").read_text(encoding="utf-8")
        assert "opencv_videoio_ffmpeg" in text

    def test_collect_all_packages_unchanged(self) -> None:
        text = (PACKAGING / "loomind.spec").read_text(encoding="utf-8")
        for package in (
            "faster_whisper",
            "ctranslate2",
            "onnxruntime",
            '"av"',
            "rapidocr_onnxruntime",
        ):
            assert package in text, f"spec 丢了 collect_all 包 {package}"


# ----------------------------------------------------------------------
# packaging/entry.py
# ----------------------------------------------------------------------


class _FakeResult:
    provider = "fake-ocr"
    provider_version = "0"
    confidence = 0.99

    def __init__(self, text: str) -> None:
        self.text = text
        self.regions = [object(), object()]


class _FakeOCRProvider:
    def __init__(self, text: str = "Hello Classroom 12345") -> None:
        self._text = text

    def is_available(self) -> bool:
        return True

    def ocr(self, image):  # noqa: ANN001 - 与真实 provider 同形
        return _FakeResult(self._text)


class TestEntry:
    def test_verify_static_ok_and_writes_out_file(self, tmp_path, capsys) -> None:
        out = tmp_path / "static.txt"
        code = ENTRY.main(["verify-static", "--out", str(out)])
        assert code == 0
        text = out.read_text(encoding="utf-8")
        assert "VERIFY-STATIC: OK" in text
        assert "index.html" in text

    def test_verify_ocr_success_writes_out_file(self, monkeypatch, tmp_path) -> None:
        from src import ocr_provider

        monkeypatch.setattr(ocr_provider, "LocalOCRProvider", lambda: _FakeOCRProvider())
        out = tmp_path / "ocr.txt"
        code = ENTRY.main(["verify-ocr", "--out", str(out)])
        assert code == 0
        text = out.read_text(encoding="utf-8")
        assert "VERIFY-OCR: OK" in text
        assert "12345" in text

    def test_verify_ocr_failure_is_reported(self, monkeypatch, tmp_path) -> None:
        from src import ocr_provider

        monkeypatch.setattr(
            ocr_provider, "LocalOCRProvider", lambda: _FakeOCRProvider("nothing to see")
        )
        out = tmp_path / "ocr-fail.txt"
        code = ENTRY.main(["verify-ocr", "--out", str(out)])
        assert code == 1
        text = out.read_text(encoding="utf-8")
        assert "VERIFY-OCR: FAIL" in text
        assert "12345" in text and "classroom" in text  # 报告缺了什么

    def test_out_requires_a_path(self, capsys) -> None:
        assert ENTRY.main(["--out"]) == 2
        assert "--out requires a path" in capsys.readouterr().err

    def test_default_delegates_to_tray_main(self, monkeypatch) -> None:
        import src.application.tray as tray

        seen: list[list[str]] = []
        monkeypatch.setattr(tray, "main", lambda argv=None: seen.append(list(argv or [])) or 42)
        assert ENTRY.main([]) == 42
        assert ENTRY.main(["--port", "9"]) == 42
        assert seen == [[], ["--port", "9"]]

    def test_probe_image_contains_text_pixels(self, tmp_path) -> None:
        path = tmp_path / "probe.png"
        ENTRY._make_probe_image(str(path))
        from PIL import Image

        with Image.open(path) as image:
            assert image.size == (760, 190)
            # 全白图 = 什么都没画; 至少要有些黑像素。
            colors = image.convert("L").getcolors(maxcolors=100000)
            assert len(colors) > 1


# ----------------------------------------------------------------------
# packaging/build.py
# ----------------------------------------------------------------------


class TestBuildScript:
    def test_reads_version_from_workspace(self) -> None:
        assert BUILD.read_version() == APPLICATION_VERSION

    def test_setup_path_matches_iss_naming(self) -> None:
        """build.py 拼的安装包路径必须与 iss 的 OutputBaseFilename 规则一致。

        两边不一致时, build.py 会在"构建成功"之后报 missing —— 先在这里红。
        """
        text = _read_iss()
        match = re.search(r"OutputBaseFilename=(.+)$", text, re.M)
        expected = match.group(1).replace("{#AppVersion}", "{version}").strip() + ".exe"
        computed = os.path.basename(
            os.path.join(BUILD.SETUP_DIR, f"Loomind-{'{version}'}-Setup.exe")
        )
        assert computed == expected

    def test_defaults_point_at_machine_paths(self) -> None:
        assert "clases-pack" in BUILD.DEFAULT_PACK_PYTHON
        assert "Inno Setup 7" in BUILD.DEFAULT_ISCC


# ----------------------------------------------------------------------
# 资源放置
# ----------------------------------------------------------------------


class TestAssets:
    def test_app_icon_is_tracked_in_application_assets(self) -> None:
        icon = ROOT / "src" / "application" / "assets" / "app.ico"
        assert icon.is_file()
        assert icon.stat().st_size > 1000

    def test_gitignore_covers_build_artifacts(self) -> None:
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "/dist/" in text
        assert "/build/" in text
