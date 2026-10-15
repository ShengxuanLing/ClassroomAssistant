# -*- mode: python ; coding: utf-8 -*-
"""TASK-110 正式打包 spec (PyInstaller onedir)。

构建: ``python packaging/build.py`` (它封装了本 spec + Inno Setup);
单独用 PyInstaller 也可以::

    pyinstaller --noconfirm --clean \
        --distpath dist --workpath build/pyinstaller \
        packaging/classroom-assistant.spec

收集策略 (与验证过的 spike 一致, 见 temp/pack_spike/spike.spec)
--------------------------------------------------------------------

- ``src/web`` 与 ``src/application/assets`` 作为 datas 收进包内, 冻结后
  ``default_static_dir()`` / ``tray.default_icon_path()`` 的 ``__file__``
  推导天然成立;
- ASR / OCR 五件套走 ``collect_all`` —— 它们的动态加载方式静态分析抓不到;
- 裁掉 ``opencv_videoio_ffmpeg*`` (30 MB): rapidocr 只用 cv2 的图像处理
  (``imdecode`` / ``resize`` / ``cvtColor``), 从不碰 VideoCapture, 实测
  裁剪后 OCR 结果逐位一致 (temp/pack_spike);
- ``console=False``: 安装版是 B 档形态, 窗口程序 + 托盘 (tray.py), 诊断
  探针把结果写文件 (packaging/entry.py)。
"""

import os

from PyInstaller.utils.hooks import collect_all

# SPECPATH 由 PyInstaller 注入: 本文件所在目录。ROOT = 仓库根
# (spec 位于 packaging/ 下, 上跳一层)。
ROOT = os.path.dirname(os.path.abspath(SPECPATH))

datas = [
    (os.path.join(ROOT, "src", "web"), "src/web"),
    (os.path.join(ROOT, "src", "application", "assets"), "src/application/assets"),
]
binaries = []
hiddenimports = []

for package in (
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "av",
    "rapidocr_onnxruntime",
):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# 见模块 docstring: 只用图像处理, 视频 IO 那支 FFmpeg DLL (30 MB) 可裁。
_EXCLUDED_BINARIES = ("opencv_videoio_ffmpeg",)

ICON = os.path.join(ROOT, "src", "application", "assets", "app.ico")

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

a.binaries = TOC(
    [item for item in a.binaries if not any(flag in item[0] for flag in _EXCLUDED_BINARIES)]
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClassroomAssistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ClassroomAssistant",
)
