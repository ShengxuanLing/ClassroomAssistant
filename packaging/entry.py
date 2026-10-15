# -*- coding: utf-8 -*-
"""PyInstaller 冻结入口 (TASK-110 — Windows 安装包)。

默认行为: 启动托盘 (``src.application.tray.main``) —— 安装版是
``--noconsole`` 的窗口程序, 启动即常驻托盘并打开浏览器。

诊断模式 (验收与排查用)
--------------------------------------------------------------------

窗口程序没有控制台, ``print`` 写进虚空; 所以探针把结果**落文件**:

    Loomind.exe verify-ocr [--out PATH]
    Loomind.exe verify-static [--out PATH]

- ``verify-ocr``: **真跑一次 OCR** —— 现场生成一张带文字的小图, 交给
  本地 OCR provider 识别, 检查数字串 ``12345`` 与单词 ``classroom`` 都能
  读出来。它同时要求"文字检测"和"文字识别"两个环节都工作, 只 import
  成功是拿不到这个串的;
- ``verify-static``: 检查内置 Web 资源 (``src/web``) 在冻结环境里能被
  定位到 —— 界面能打开的前提。

``--out`` 省略时只打印 (终端运行时可见); 给了路径则同内容写文件, 供
无控制台的等装版验收脚本 grep。
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Sequence

__all__ = ["main", "verify_ocr", "verify_static"]


def _emit(lines: Sequence[str], out_path: Optional[str]) -> None:
    text = "\n".join(lines) + "\n"
    stream = getattr(sys, "stdout", None)
    if stream is not None:
        try:
            stream.write(text)
            stream.flush()
        except Exception:  # noqa: BLE001 - 窗口程序 stdout 可能不可写
            pass
    if out_path:
        with open(out_path, "w", encoding="utf-8") as handle:
            handle.write(text)


def _font(size: int):
    from PIL import ImageFont

    for candidate in ("arial.ttf", "segoeui.ttf", "calibri.ttf"):
        try:
            return ImageFont.truetype("C:/Windows/Fonts/" + candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _make_probe_image(path: str) -> str:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (760, 190), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    draw.text((30, 58), "Hello Classroom 12345", fill=(0, 0, 0), font=_font(62))
    image.save(path)
    return path


def verify_ocr(out_path: Optional[str] = None) -> int:
    """真实 OCR 探针: 生成图 -> 识别 -> 逐项检查结果。"""
    import tempfile

    from src.ocr_provider import ImageInput, LocalOCRProvider

    workdir = tempfile.mkdtemp(prefix="ca-verify-")
    probe = _make_probe_image(os.path.join(workdir, "probe.png"))

    provider = LocalOCRProvider()
    result = provider.ocr(ImageInput(path=probe))

    lines = [
        "probe image   : " + probe,
        "is_available  : " + str(provider.is_available()),
        "provider      : %s | %s" % (result.provider, result.provider_version),
        "confidence    : %s" % result.confidence,
        "regions       : %d" % len(result.regions),
        "TEXT          : %r" % result.text,
    ]

    squeezed = result.text.replace(" ", "").lower()
    missing = [token for token in ("12345", "classroom") if token not in squeezed]
    if missing:
        lines.append("VERIFY-OCR: FAIL - missing %s" % ", ".join(missing))
        _emit(lines, out_path)
        return 1
    lines.append("VERIFY-OCR: OK")
    _emit(lines, out_path)
    return 0


def verify_static(out_path: Optional[str] = None) -> int:
    """静态资源探针: 冻结环境里 ``src/web`` 是否还找得到。"""
    from src.api.server import default_static_dir

    directory = default_static_dir()
    lines = ["static_dir    : " + directory, "is_dir        : " + str(os.path.isdir(directory))]

    missing = []
    for name in ("index.html", "styles.css", "app.js", "api.js", "i18n.js"):
        path = os.path.join(directory, name)
        ok = os.path.isfile(path)
        if not ok:
            missing.append(name)
        lines.append("  %-12s %s" % (name, "OK" if ok else "MISSING"))

    if missing:
        lines.append("VERIFY-STATIC: FAIL - missing %s" % missing)
        _emit(lines, out_path)
        return 1
    lines.append("VERIFY-STATIC: OK")
    _emit(lines, out_path)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    out_path: Optional[str] = None
    if "--out" in args:
        index = args.index("--out")
        try:
            out_path = args[index + 1]
        except IndexError:
            print("--out requires a path", file=sys.stderr)
            return 2
        del args[index : index + 2]

    if args[:1] == ["verify-ocr"]:
        return verify_ocr(out_path)
    if args[:1] == ["verify-static"]:
        return verify_static(out_path)

    from src.application.tray import main as tray_main

    return tray_main(args)


if __name__ == "__main__":  # pragma: no cover - 冻结后由 bootloader 调用
    raise SystemExit(main())
