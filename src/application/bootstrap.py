# -*- coding: utf-8 -*-
"""运行时装配 (组合根, Task 44)。

这是**唯一**允许同时认识 ``persistence`` / ``backup`` / ``api`` 的地方。
把它单独放一个文件, 而不是散在业务服务里, 是为了让"谁依赖了存储层"这件事
一眼可见 —— ``tests/test_persistence_layering.py`` 会断言应用层里只有本文件
可以 import ``src.persistence``。

本文件做什么
--------------------------------------------------------------------

按 ``AppConfig`` 把整个进程装配起来:

1. 建立数据目录布局 (``ensure_data_layout``);
2. 打开并迁移 SQLite 库 (``open_database``) —— 因此 ``database_path``
   是一个**真被用到**的配置项, 不是摆设;
3. 按 ``whisper_*`` / ``ocr_config`` 构造 ASR / OCR provider;
4. 构造 ``Workspace`` (多课程组合根) 与 ``ApiServer``;
5. 构造 ``BackupService`` (Task 43), 绑定同一个 data_dir。

诚实边界 (必须说清楚, 否则就是夸大)
--------------------------------------------------------------------

配置驱动的是**数据库的创建/迁移与备份**, 以及运行参数 (端口/上传上限/
日志级别/调试开关)。业务对象 (Course / Material / Evidence /
KnowledgePoint / Student ...) 仍然由内存中的领域服务持有 ——
把它们映射到 SQLite 属于**既有引擎**的持久化改造, 不在 Task 44 范围内,
本任务没有改写核心引擎。所以: 备份归档里的数据库是**真实、可迁移、可恢复
的库**, 只是它的表当前还没有被业务写入。

``asr_mode="auto"`` 的语义
--------------------------------------------------------------------

有真实 faster-whisper 就用真实; 没有则回落 Mock, 但**绝不静默**:
``asr_mode`` 会进入 ``/api/health``, 同时产生一条 ``runtime_note`` 警告。
``asr_mode="real"`` 则在运行时缺失时**直接报错** —— 明确要求真货却拿到
Mock, 是最不该被容忍的那种"看起来在工作"。

为什么 ``src.api`` 是延迟 import
--------------------------------------------------------------------

``src.api.server`` 依赖 ``src.application.workspace``。本文件若在模块顶层
import ``src.api``, 一旦将来有人把 ``bootstrap`` 加进
``src.application.__init__``, 就会形成
``src.application -> bootstrap -> src.api -> src.application.workspace``
的环。延迟到函数内部, 既避开了这个雷, 也让 ``--print-config`` 这种命令
不必为 HTTP 层付出 import 成本。
"""

from __future__ import annotations

import importlib.util
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from src.application.config import AppConfig
from src.application.data_dirs import DataLayout, ensure_data_layout
from src.application.errors import ConfigurationError
from src.application.logging_setup import configure_logging, get_logger, log_event
from src.application.persistence_wiring import WorkspacePersistence
from src.application.runtime import Clock, utc_now_iso
from src.application.workspace import APPLICATION_NAME, APPLICATION_VERSION, Workspace
from src.backup import BackupService
from src.ocr_provider import LocalOCRProvider, ProviderOCREngine
from src.persistence import Database, open_database

__all__ = [
    "APPLICATION_LOGGER_NAME",
    "Runtime",
    "build_runtime",
    "close_runtime",
    "describe_runtime",
    "is_local_whisper_available",
    "LLM_MODES",
]

#: 本项目自己的 logger 名 (``component`` 字段)。
APPLICATION_LOGGER_NAME = "classroom"

#: 真实 ASR 运行时对应的发行包名。
WHISPER_RUNTIME_PACKAGE = "faster_whisper"


def is_local_whisper_available() -> bool:
    """真实 ASR 运行时是否可导入 (只探测, 不加载模型)。

    与 ``src.ocr_provider.is_local_ocr_available`` 同一套路: 用
    ``find_spec`` 判断"装没装", 而不是 import 之后才知道 —— 后者会把
    import 失败的副作用带进运行期。
    """
    try:
        return importlib.util.find_spec(WHISPER_RUNTIME_PACKAGE) is not None
    except (ImportError, ValueError):  # pragma: no cover - 环境异常
        return False


@dataclass
class Runtime:
    """一个已装配好的应用进程。

    ``server`` 尚未 ``start()`` —— 绑定端口是 ``main()`` 的动作, 这样测试
    可以完整装配一遍而不占端口。
    """

    config: AppConfig
    layout: DataLayout
    database: Database
    backup: BackupService
    workspace: Workspace
    server: Any
    logger: logging.Logger
    asr_mode: str
    ocr_mode: str
    llm_mode: str = "mock"
    ai_mode: str = "disabled"
    notes: tuple[str, ...] = ()

    # -- 便于测试用 ``with build_runtime(...) as runtime:`` ----------------

    def __enter__(self) -> "Runtime":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        close_runtime(self)


def _build_asr_provider(config: AppConfig, notes: list[str]) -> tuple[Any, str]:
    """按 ``asr_mode`` / ``whisper_*`` 构造 ASR provider, 返回 ``(provider, 模式)``。"""
    from src.asr_provider import MockASRProvider

    if config.asr_mode == "mock":
        return MockASRProvider(), "mock"

    available = is_local_whisper_available()
    if config.asr_mode == "real":
        if not available:
            raise ConfigurationError(
                "asr_mode='real' but the real ASR runtime is not installed "
                f"({WHISPER_RUNTIME_PACKAGE}); install it or use asr_mode='auto'",
                detail={"asr_mode": config.asr_mode, "package": WHISPER_RUNTIME_PACKAGE},
            )
    elif not available:
        notes.append(
            f"asr_mode='auto' fell back to Mock ASR: {WHISPER_RUNTIME_PACKAGE} is not "
            "installed, so transcribed text is NOT from a real Whisper model"
        )
        return MockASRProvider(), "mock"

    from src.whisper_provider import create_local_whisper_provider

    return create_local_whisper_provider(config.whisper_config()), "real"


def _build_ocr_engine(config: AppConfig, notes: list[str]) -> tuple[Any, str]:
    """按 ``ocr_config`` 构造 OCR 引擎, 返回 ``(引擎, 模式)``。

    模式由**实际拿到的 provider 类型**推导, 而不是把工厂里的判断再抄一遍
    —— 这样"工厂回落到 Mock"这件事不可能被配置层说成 "real"。
    """
    provider = config.ocr_provider()
    mode = "real" if isinstance(provider, LocalOCRProvider) else "mock"
    if mode == "mock" and config.ocr_config.get("kind", "auto") == "auto":
        notes.append(
            "ocr_config.kind='auto' fell back to Mock OCR: no local OCR engine is "
            "installed, so extracted text is NOT from a real OCR model"
        )
    return ProviderOCREngine(provider), mode


#: LLM 模式 (显式 bootstrap 参数, 不新增环境变量): mock / real / auto。
LLM_MODES = ("mock", "real", "auto")


def _build_summarizer(
    config: AppConfig, llm_mode: str, notes: list[str]
) -> tuple[Any, str]:
    """按显式 ``llm_mode`` 装配 LLM 总结提供方, 返回 ``(provider, 模式)``。

    - ``mock``: 永远 Mock (默认; 默认回归全绿、零出站)。
    - ``real``: 缺 ``CLASSROOM_LLM_API_KEY`` 即 ``ConfigurationError``,
      **禁静默降级** (要求真货却拿到 Mock 是最不该被容忍的"看起来在工作")。
    - ``auto``: 有 key 用 real, 没有 则回落 Mock 但**绝不静默**
      (记一条 runtime_note, health 的 ``llm.mode`` 标 ``mock``)。
    """
    from src.llm_provider import MockSummarizer, OpenAICompatibleProvider

    if llm_mode == "mock":
        return MockSummarizer(), "mock"

    has_key = bool(config.llm_api_key)
    if llm_mode == "real" and not has_key:
        raise ConfigurationError(
            "llm_mode='real' requires CLASSROOM_LLM_API_KEY to be set in the "
            "process environment; refusing to silently fall back to mock",
            detail={"llm_mode": "real"},
        )
    if not has_key:
        notes.append(
            "llm_mode='auto' fell back to Mock summarizer: CLASSROOM_LLM_API_KEY "
            "is not set, so summaries are NOT from a real LLM"
        )
        return MockSummarizer(), "mock"
    provider = OpenAICompatibleProvider(
        api_key=config.llm_api_key or "",
        api_base=config.llm_api_base,
        model=config.llm_model,
    )
    return provider, "real"


def _build_ai_provider(notes: list[str]) -> tuple[Any, str]:
    """按 ``CLASSROOM_AI_*`` 环境装配 AI 语义理解提供方 (TASK-77 §16)。

    返回 ``(provider, ai_mode)``, 其中 ``ai_mode`` 为 ``disabled`` /
    ``fake`` / ``real`` 之一 (进 ``/api/health`` 的可观测状态, 绝不静默):

    - ``CLASSROOM_AI_ENABLED`` 未开 -> ``(None, "disabled")``: 旧确定性
      链路, ``process_material`` 不触发任何 AI 调用;
    - 已开且有真实凭证 (``CLASSROOM_AI_API_KEY`` + https base URL, 未配时
      回落 ``CLASSROOM_LLM_*``) -> OpenAI-compatible provider, ``"real"``;
    - 已开但无凭证 -> 确定性 ``FakeAIProvider``, ``"fake"`` (记一条
      runtime_note, 与 ASR/OCR 的"诚实回落"同口径)。

    Key 只读进程环境 (``src/application/ai/config.get_api_key``), 绝不进
    日志 / 描述 / 异常 (``notes`` 里只出现"有/无", 无明文)。
    图片字节默认不出境 (``CLASSROOM_AI_ALLOW_IMAGE_BYTES`` 未开时 provider
    的 vision 门关闭, 图片走 OCR 文字)。
    """
    from src.application.ai.config import get_api_key, load_ai_config
    from src.application.ai.provider import FakeAIProvider

    config = load_ai_config()
    if not config.enabled:
        return None, "disabled"
    if config.has_credentials:
        from src.application.ai.provider import OpenAICompatibleAIProvider

        key = get_api_key() or ""
        provider = OpenAICompatibleAIProvider(
            api_key=key,
            base_url=config.base_url or "",
            model=config.text_model or "",
            vision_model=config.vision_model,
            audio_model=config.audio_model,
            max_retries=config.max_retries,
            json_mode=config.json_mode,
            allow_image_bytes=config.allow_image_bytes,
        )
        return provider, "real"
    notes.append(
        "CLASSROOM_AI_ENABLED=true but no API key is configured: "
        "using the deterministic FakeAIProvider, so knowledge points are "
        "NOT from a real model"
    )
    return FakeAIProvider(), "fake"


def build_runtime(
    config: AppConfig,
    *,
    clock: Optional[Clock] = None,
    logger: Optional[logging.Logger] = None,
    log_stream: Optional[Any] = None,
    llm_mode: str = "mock",
) -> Runtime:
    """按配置装配整个运行时。

    不绑定端口 —— 调用方拿到 ``Runtime`` 后自行决定何时 ``server.start()``。

    ``llm_mode`` 是显式参数 (mock / real / auto), 默认 ``mock``: 没有
    显式要求就绝不出站。
    """
    if not isinstance(config, AppConfig):
        raise ConfigurationError(
            f"build_runtime expects an AppConfig (got {type(config).__name__})"
        )
    if llm_mode not in LLM_MODES:
        raise ConfigurationError(
            f"llm_mode must be one of {list(LLM_MODES)} (got {llm_mode!r})",
            detail={"llm_mode": llm_mode},
        )

    resolved_clock: Clock = clock or utc_now_iso
    layout = ensure_data_layout(config.data_dir)
    os.makedirs(config.database_dir, exist_ok=True)

    if logger is None:
        configure_logging(config, stream=log_stream, clock=resolved_clock)
        logger = get_logger(APPLICATION_LOGGER_NAME)

    notes: list[str] = []
    asr_provider, asr_mode = _build_asr_provider(config, notes)
    ocr_engine, ocr_mode = _build_ocr_engine(config, notes)
    summarizer, resolved_llm_mode = _build_summarizer(config, llm_mode, notes)
    ai_provider, ai_mode = _build_ai_provider(notes)

    database = open_database(config.database_path, clock=resolved_clock)
    backup = BackupService(
        config.data_dir,
        database_filename=config.database_filename,
        clock=resolved_clock,
        application_version=APPLICATION_VERSION,
        config=config.public_dict(),
    )
    workspace = Workspace(
        config.data_dir,
        clock=resolved_clock,
        max_file_size=config.max_upload_size,
        asr_provider=asr_provider,
        ocr_engine=ocr_engine,
        asr_mode=asr_mode,
        ocr_mode=ocr_mode,
        summarizer=summarizer,
        llm_mode=resolved_llm_mode,
        # 复用同一个 Database 连接 (全进程一个组合根, 一个连接):
        # Workspace 因此不会自己再开一个库, close_runtime 也只需要关一次。
        persistence=WorkspacePersistence.for_database(
            database, clock=resolved_clock
        ),
    )
    # TASK-77: AI 自动管线按环境装配。``disabled`` 时 Workspace 保持
    # TASK-76 的默认关闭形状; ``fake``/``real`` 时打开自动触发
    # (process_material 摄取成功后自动分析, 失败不污染材料)。
    if ai_mode != "disabled":
        workspace.configure_ai(enabled=True, provider=ai_provider)

    from src.api.server import create_server

    server = create_server(
        workspace,
        host=config.host,
        port=config.port,
        max_upload_bytes=config.max_upload_size,
        debug=config.debug,
        logger=logger,
    )

    runtime = Runtime(
        config=config,
        layout=layout,
        database=database,
        backup=backup,
        workspace=workspace,
        server=server,
        logger=logger,
        asr_mode=asr_mode,
        ocr_mode=ocr_mode,
        llm_mode=resolved_llm_mode,
        ai_mode=ai_mode,
        notes=tuple(notes),
    )

    for note in notes:
        log_event(logger, logging.WARNING, "runtime_note", note)
    log_event(
        logger,
        logging.INFO,
        "runtime_ready",
        f"{APPLICATION_NAME} {APPLICATION_VERSION} ready",
        detail=describe_runtime(runtime),
    )
    return runtime


def describe_runtime(runtime: Runtime) -> dict[str, Any]:
    """运行时的可展示快照 (用于启动日志与 ``/api/health``)。"""
    return {
        "application": APPLICATION_NAME,
        "version": APPLICATION_VERSION,
        "data_dir": runtime.config.data_dir,
        "database_path": runtime.config.database_path,
        "database_schema_version": runtime.database.schema_version(),
        "backups_dir": runtime.config.backups_dir,
        "asr_mode": runtime.asr_mode,
        "ocr_mode": runtime.ocr_mode,
        "llm_mode": runtime.llm_mode,
        "llm_model": getattr(runtime.workspace.summarizer, "model", None) or "mock-extractive",
        "ai_mode": runtime.ai_mode,
        "ai_enabled": bool(runtime.workspace.ai_enabled),
        "whisper_model": runtime.config.whisper_model,
        "whisper_device": runtime.config.whisper_device,
        "host": runtime.config.host,
        "port": runtime.server.port,
        "log_level": runtime.config.log_level,
        "debug": runtime.config.debug,
        "notes": list(runtime.notes),
    }


def close_runtime(runtime: Runtime) -> None:
    """停止服务器 (若已启动) 并关闭数据库。可重复调用。"""
    server = runtime.server
    if server is not None and getattr(server, "running", False):
        server.stop()
    workspace = getattr(runtime, "workspace", None)
    if workspace is not None:
        # Workspace 只是**复用**了组合根的连接 (owns_database=False), 所以
        # 这里先让它停止使用连接, 再由组合根关闭 —— 顺序反过来会出现
        # "关掉之后还有人在写"。
        workspace.close()
    if not runtime.database.closed:
        runtime.database.close()
