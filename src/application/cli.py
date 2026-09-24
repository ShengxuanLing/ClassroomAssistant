# -*- coding: utf-8 -*-
"""命令行入口 (Task 44)。

职责刻意很窄: **解析 argv -> 交给 AppConfig -> 交给 bootstrap**。
本模块自己不做任何校验 —— 校验只有一处 (``AppConfig.validate``), 否则
"CLI 说合法、配置文件说不合法"这种错位迟早出现。

``--print-config`` 与 ``--check`` 是给运维/排查用的
--------------------------------------------------------------------

- ``--print-config``: 打印**最终生效**的配置, 并标注每个值来自哪一层。
  它**不**打开数据库、不绑定端口, 所以数据库坏了也能用。
- ``--check``: 完整装配一遍运行时 (建目录 / 开库迁移 / 构造 provider /
  绑定端口前的一切), 打印快照后退出。Task 46 的 ``health`` 脚本就是调它。

Windows 优先
--------------------------------------------------------------------

- 控制台编码可能是 GBK/cp1252, 而 data_dir 路径里可能含西语/加泰语字符。
  输出走 :func:`_emit`, 编码不了就降级替换 —— **绝不因为一条路径里有
  重音字符就让整个程序启动失败**。
- 端口占用等 ``OSError`` 被翻成友好提示, 而不是一段 traceback。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from src.application.config import (
    AppConfig,
    CONFIGURABLE_KEYS,
    DEFAULT_DATA_DIR_NAME,
    LOG_LEVELS,
    ASR_MODES,
    OCR_KINDS,
    load_local_environment,
)
from src.application.errors import ApplicationError, ConfigurationError, PortInUseError
from src.application.logging_setup import configure_logging, log_event
from src.application.workspace import APPLICATION_NAME, APPLICATION_VERSION

__all__ = [
    "CliOptions",
    "build_parser",
    "parse_cli",
    "build_config",
    "run",
    "main",
    "EXIT_OK",
    "EXIT_CONFIG_ERROR",
    "EXIT_STARTUP_ERROR",
    "EXIT_RUNTIME_ERROR",
]

EXIT_OK = 0
EXIT_CONFIG_ERROR = 2
EXIT_STARTUP_ERROR = 3
EXIT_RUNTIME_ERROR = 4


@dataclass(frozen=True)
class CliOptions:
    """解析结果: 配置文件位置 + 覆盖项 + 动作开关。"""

    config_path: Optional[str] = None
    overrides: Mapping[str, Any] = field(default_factory=dict)
    print_config: bool = False
    check_only: bool = False
    json_output: bool = False

    @property
    def wants_debug(self) -> bool:
        """是否要求暴露 debug 信息 (未显式指定时为 False)。"""
        return bool(self.overrides.get("debug", False))


def build_parser() -> argparse.ArgumentParser:
    """构造 argparse parser。

    所有覆盖项默认值都是 ``None`` —— 这样"没给这个开关"与"给了 False"
    (``--no-debug``) 才能区分开。否则 ``store_true`` 的默认 False 会把
    配置文件里的 ``debug=true`` 悄悄压掉。
    """
    parser = argparse.ArgumentParser(
        prog="classroom-assistant",
        description=f"{APPLICATION_NAME} —— 本地单机课堂材料助手",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--version", action="version", version=f"{APPLICATION_NAME} {APPLICATION_VERSION}"
    )
    parser.add_argument(
        "--config", dest="config_path", default=None, metavar="PATH",
        help="配置文件 (JSON); 未指定时在 data_dir 下查找 "
             f"classroom-assistant.json",
    )
    parser.add_argument(
        "--data-dir", dest="data_dir", default=None, metavar="DIR",
        help=f"数据根目录 (默认: <cwd>/{DEFAULT_DATA_DIR_NAME})",
    )
    parser.add_argument(
        "--database-path", dest="database_path", default=None, metavar="PATH",
        help="SQLite 路径 (必须位于 data_dir 内; 默认 data_dir/database/classroom.sqlite)",
    )
    parser.add_argument("--host", default=None, help="监听地址 (默认回环 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="监听端口 (0 = 由系统分配)")
    parser.add_argument(
        "--max-upload-size", dest="max_upload_size", type=int, default=None,
        metavar="BYTES", help="单次上传字节上限",
    )
    parser.add_argument(
        "--whisper-model", dest="whisper_model", default=None,
        help="Whisper 模型名 (tiny/base/small/medium/large-v1/large-v2/large-v3/distil-large-v3)",
    )
    parser.add_argument(
        "--whisper-device", dest="whisper_device", default=None, help="cpu 或 cuda"
    )
    parser.add_argument(
        "--whisper-compute-type", dest="whisper_compute_type", default=None,
        help="int8 / float16 / float32 / int8_float16",
    )
    parser.add_argument(
        "--whisper-language", dest="whisper_language", default=None,
        help="ISO 639-1/639-2 语言码; 传空串表示显式置空",
    )
    parser.add_argument(
        "--asr-mode", dest="asr_mode", default=None,
        help=f"{'/'.join(ASR_MODES)} (auto = 有真实运行时就用真实, 否则回落 Mock)",
    )
    parser.add_argument(
        "--ocr-config", dest="ocr_config", default=None, metavar="JSON",
        help=f"OCR 配置 JSON 对象, kind 取 {'/'.join(OCR_KINDS)}",
    )
    parser.add_argument(
        "--log-level", dest="log_level", default=None,
        help=f"{'/'.join(LOG_LEVELS)}",
    )
    parser.add_argument(
        "--debug", dest="debug", action="store_true", default=None,
        help="开发模式: 用户可见错误里保留原始描述",
    )
    parser.add_argument(
        "--no-debug", dest="debug", action="store_false", default=None,
        help="生产模式 (默认): 用户只看到友好错误, 细节进日志",
    )
    parser.add_argument(
        "--allow-remote", dest="allow_remote", action="store_true", default=None,
        help="允许绑定非回环地址 (默认拒绝, 本应用是单机单用户)",
    )
    parser.add_argument(
        "--no-allow-remote", dest="allow_remote", action="store_false", default=None,
        help="强制只允许回环地址 (默认)",
    )
    parser.add_argument(
        "--print-config", action="store_true", help="打印最终配置后退出 (不打开数据库)"
    )
    parser.add_argument(
        "--check", dest="check_only", action="store_true",
        help="完整装配运行时并打印快照后退出 (供 health 脚本使用)",
    )
    parser.add_argument("--json", dest="json_output", action="store_true", help="以 JSON 输出")
    return parser


def parse_cli(argv: Optional[Sequence[str]] = None) -> CliOptions:
    """解析 argv (``None`` = 用 ``sys.argv[1:]``)。

    ``--help`` / ``--version`` 会像常规 CLI 一样抛 ``SystemExit``。
    """
    namespace = build_parser().parse_args(list(argv) if argv is not None else None)
    raw = vars(namespace)
    overrides = {
        key: value
        for key, value in raw.items()
        if key in CONFIGURABLE_KEYS and value is not None
    }
    return CliOptions(
        config_path=raw.get("config_path"),
        overrides=overrides,
        print_config=bool(raw.get("print_config")),
        check_only=bool(raw.get("check_only")),
        json_output=bool(raw.get("json_output")),
    )


def build_config(
    options: CliOptions,
    *,
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[str] = None,
) -> AppConfig:
    """把解析结果交给配置层 (唯一校验入口)。"""
    # The batch wrapper and an IDE/debug launch must observe the same local
    # AI environment.  Never do this for an explicitly supplied test mapping
    # (or under pytest), so fixtures cannot inherit a developer's credentials.
    if env is None and not os.environ.get("PYTEST_CURRENT_TEST"):
        load_local_environment()
    return AppConfig.load(
        config_path=options.config_path,
        env=env,
        cli_overrides=options.overrides,
        cwd=cwd,
    )


# ----------------------------------------------------------------------
# 输出 (Windows 编码安全)
# ----------------------------------------------------------------------


def _emit(text: str, *, stream: Optional[Any] = None) -> None:
    """向控制台写一行。

    Windows 控制台默认可能是 GBK/cp1252, 而 data_dir 里可能有
    ``Álgebra`` 这样的路径。编码不了就**降级替换**, 而不是抛
    ``UnicodeEncodeError`` 让程序在启动阶段就死掉。
    """
    target = stream if stream is not None else sys.stdout
    line = text if text.endswith("\n") else text + "\n"
    try:
        target.write(line)
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", None) or "utf-8"
        target.write(line.encode(encoding, "replace").decode(encoding, "replace"))
    flush = getattr(target, "flush", None)
    if callable(flush):
        flush()


def _emit_error(text: str) -> None:
    _emit(text, stream=sys.stderr)


def _format_config(config: AppConfig, *, as_json: bool) -> str:
    if as_json:
        return json.dumps(config.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    return config.describe()


# ----------------------------------------------------------------------
# 运行
# ----------------------------------------------------------------------


def run(options: CliOptions, *, env: Optional[Mapping[str, str]] = None,
        cwd: Optional[str] = None) -> int:
    """执行一次 CLI 调用, 返回退出码 (不抛 ``SystemExit``)。"""
    try:
        config = build_config(options, env=env, cwd=cwd)
    except ApplicationError as exc:
        _emit_error(f"[{exc.code}] {exc.message}")
        if options.wants_debug:
            traceback.print_exc()
        return EXIT_CONFIG_ERROR

    if options.print_config:
        _emit(_format_config(config, as_json=options.json_output))
        return EXIT_OK

    # 延迟 import: ``--print-config`` 不该把 HTTP / 存储层拉起来。
    from src.application.bootstrap import build_runtime, close_runtime, describe_runtime

    configure_logging(config)
    logger = logging.getLogger("classroom")

    try:
        runtime = build_runtime(config, logger=logger)
    except ApplicationError as exc:
        _emit_error(f"[{exc.code}] {exc.message}")
        if config.debug:
            traceback.print_exc()
        return EXIT_STARTUP_ERROR
    except OSError as exc:
        _emit_error(f"startup failed: {exc}")
        if config.debug:
            traceback.print_exc()
        return EXIT_STARTUP_ERROR

    try:
        if options.check_only:
            snapshot = describe_runtime(runtime)
            if options.json_output:
                _emit(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
            else:
                for key in sorted(snapshot):
                    _emit(f"  {key:<24} = {snapshot[key]!r}")
            return EXIT_OK

        try:
            runtime.server.start()
        except PortInUseError as exc:
            # 端口占用: 友好提示, 绝不 traceback (Task 46 Graceful failure)。
            _emit_error(str(exc.message))
            return EXIT_STARTUP_ERROR
        except OSError as exc:
            _emit_error(f"startup failed: {exc}")
            return EXIT_STARTUP_ERROR

        log_event(
            logger, logging.INFO, "server_listening", f"listening on {runtime.server.url}",
            detail={"url": runtime.server.url, "debug": config.debug},
        )
        _emit(f"{APPLICATION_NAME} {APPLICATION_VERSION} listening on {runtime.server.url}")
        _emit("按 Ctrl+C 停止。")
        try:
            runtime.server.serve_forever()
        except KeyboardInterrupt:
            _emit("stopping ...")
        return EXIT_OK
    finally:
        close_runtime(runtime)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """``python -m src.application.cli`` 的入口。"""
    return run(parse_cli(argv))


if __name__ == "__main__":  # pragma: no cover - 手动运行
    raise SystemExit(main())
