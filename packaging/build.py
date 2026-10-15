# -*- coding: utf-8 -*-
"""TASK-110 一键构建: PyInstaller (onedir) -> Inno Setup (Setup.exe)。

用法 (任何 Python 3.10+ 都能执行本脚本 —— 它只是调度器)::

    python packaging/build.py
    python packaging/build.py --skip-installer
    python packaging/build.py --python C:\\path\\to\\python.exe --iscc C:\\path\\to\\ISCC.exe

打包解释器解析次序 (第一个通过 ``import PyInstaller`` 检查的生效):

1. ``--python``;
2. 环境变量 ``CLASSROOM_PACK_PYTHON``;
3. 开发机默认打包 venv (``C:/Users/Rafae/.workbuddy-ai/.../clases-pack``);
4. ``sys.executable``。

ISCC 同理: ``--iscc`` > ``CLASSROOM_ISCC`` > ``D:/Program Files/Inno Setup 7/ISCC.exe``
> ``Program Files (x86)/Inno Setup 6`` > PATH 上的 ``ISCC.exe``。

产物:

- ``dist/ClassroomAssistant/ClassroomAssistant.exe`` (onedir, 无控制台);
- ``dist/installer/ClassroomAssistant-<version>-Setup.exe``。

``--skip-installer`` / ``--skip-exe`` 分别只跑一半。退出码 0 = 全部成功。
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPEC = os.path.join(ROOT, "packaging", "classroom-assistant.spec")
ISS = os.path.join(ROOT, "packaging", "installer.iss")
DIST = os.path.join(ROOT, "dist")
EXE_DIR = os.path.join(DIST, "ClassroomAssistant")
EXE = os.path.join(EXE_DIR, "ClassroomAssistant.exe")
SETUP_DIR = os.path.join(DIST, "installer")

DEFAULT_PACK_PYTHON = (
    "C:/Users/Rafae/.workbuddy-ai/binaries/python/envs/clases-pack/Scripts/python.exe"
)
DEFAULT_ISCC = "D:/Program Files/Inno Setup 7/ISCC.exe"
ISCC_FALLBACKS = (
    "D:/Program Files (x86)/Inno Setup 6/ISCC.exe",
    "C:/Program Files (x86)/Inno Setup 6/ISCC.exe",
)


def _emit(text: str) -> None:
    print(text, flush=True)


def _run(command: list[str], *, cwd: str = ROOT) -> int:
    _emit("$ " + " ".join(f'"{part}"' if " " in part else part for part in command))
    proc = subprocess.run(command, cwd=cwd)
    return proc.returncode


def _python_can_build(candidate: str) -> bool:
    if not candidate or not os.path.isfile(candidate):
        return False
    probe = subprocess.run(
        [candidate, "-c", "import PyInstaller; print(PyInstaller.__version__)"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        return False
    _emit(f"pack python   : {candidate} (PyInstaller {probe.stdout.strip()})")
    return True


def resolve_python(explicit: Optional[str]) -> str:
    candidates = [
        explicit,
        os.environ.get("CLASSROOM_PACK_PYTHON"),
        DEFAULT_PACK_PYTHON,
        sys.executable,
    ]
    for candidate in candidates:
        if candidate and _python_can_build(candidate):
            return candidate
    raise SystemExit(
        "no python with PyInstaller found; pass --python or set CLASSROOM_PACK_PYTHON"
    )


def resolve_iscc(explicit: Optional[str]) -> str:
    candidates = [explicit, os.environ.get("CLASSROOM_ISCC"), DEFAULT_ISCC, *ISCC_FALLBACKS]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            _emit(f"iscc          : {candidate}")
            return candidate
    found = shutil.which("ISCC")
    if found:
        _emit(f"iscc          : {found}")
        return found
    raise SystemExit("ISCC.exe not found; pass --iscc or set CLASSROOM_ISCC")


def read_version() -> str:
    """产品版本唯一真源: ``src/application/workspace.py`` 的 ``APPLICATION_VERSION``。

    与 ``packaging/installer.iss`` 里的回退值一致性由
    ``tests/test_packaging.py`` 钉住 (两边漂移会红)。
    """
    path = os.path.join(ROOT, "src", "application", "workspace.py")
    with open(path, "r", encoding="utf-8") as handle:
        match = re.search(r'^APPLICATION_VERSION\s*=\s*"([^"]+)"', handle.read(), re.M)
    if not match:
        raise SystemExit(f"APPLICATION_VERSION not found in {path}")
    return match.group(1)


def _size_mb(path: str) -> str:
    total = 0
    if os.path.isdir(path):
        for base, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(base, name))
                except OSError:
                    pass
    else:
        total = os.path.getsize(path)
    return f"{total / (1024 * 1024):.1f} MB"


def build_exe(python: str) -> None:
    code = _run(
        [
            python,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            DIST,
            "--workpath",
            os.path.join(ROOT, "build", "pyinstaller"),
            SPEC,
        ]
    )
    if code != 0:
        raise SystemExit(f"PyInstaller failed with exit code {code}")
    if not os.path.isfile(EXE):
        raise SystemExit(f"PyInstaller finished but {EXE} is missing")
    _emit(f"built         : {EXE} ({_size_mb(EXE_DIR)})")


def build_installer(iscc: str, version: str) -> str:
    code = _run([iscc, f"/DAppVersion={version}", ISS])
    if code != 0:
        raise SystemExit(f"ISCC failed with exit code {code}")
    setup = os.path.join(SETUP_DIR, f"ClassroomAssistant-{version}-Setup.exe")
    if not os.path.isfile(setup):
        raise SystemExit(f"ISCC finished but {setup} is missing")
    _emit(f"built         : {setup} ({_size_mb(setup)})")
    return setup


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--python", default=None, help="打包用解释器 (需已装 PyInstaller)")
    parser.add_argument("--iscc", default=None, help="Inno Setup 编译器 ISCC.exe")
    parser.add_argument("--skip-exe", action="store_true", help="不重打 exe, 只用现有 dist")
    parser.add_argument("--skip-installer", action="store_true", help="只打 exe, 不出 Setup.exe")
    options = parser.parse_args(argv)

    version = read_version()
    _emit(f"version       : {version}")

    if not options.skip_exe:
        build_exe(resolve_python(options.python))

    if not options.skip_installer:
        if not os.path.isfile(EXE):
            raise SystemExit(f"{EXE} not found; run without --skip-exe first")
        build_installer(resolve_iscc(options.iscc), version)

    _emit("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
