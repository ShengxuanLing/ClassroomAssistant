# -*- coding: utf-8 -*-
"""应用服务层 (Task 34)。

把分散的底层能力 (Course / Material / Evidence / Knowledge / Review /
Student / Exercise / Answer / Evaluation / StudyPlan / LearningPath)
组合成稳定的业务入口。

导入约定:
- 门面: AppService (推荐, 组合所有子服务)
- 各子服务: CourseService / MaterialService / KnowledgeService /
  ReviewService / LearningService
- 错误: ApplicationError 及 8 种错误码子类
- DTO: dto.py 中的 *_to_dict / to_jsonable
"""

from src.application.errors import (
    ERROR_CODES,
    ApplicationError,
    ConfigurationError,
    ConflictError,
    InternalError,
    InvalidInputError,
    NotFoundError,
    ProcessingError,
    StorageError,
    UnsupportedError,
    map_application_error,
)
from src.application.course_service import CourseService
from src.application.material_service import MaterialService
from src.application.knowledge_service import KnowledgeService, ReviewService
from src.application.learning_service import LearningService
from src.application.learning_view import LearningViewService, normalize_language
from src.application.material_workflow import MaterialWorkflowService
from src.application.processing_service import (
    JOB_STATUSES,
    ClassroomProcessingService,
    ProcessingJob,
)
from src.application.audio_pipeline import (
    LongAudioASRProvider,
    QualityCheckedASRProvider,
)
from src.application.data_dirs import DataLayout, ensure_data_layout
from src.application.app_service import AppService
from src.application.config import (
    AppConfig,
    ConfigSource,
    load_config,
)
from src.application.logging_setup import (
    configure_logging,
    get_logger,
    log_event,
)

# 刻意**不**在这里再导出 bootstrap / cli:
# ``bootstrap`` 会 import ``src.api``, 而 ``src.api.server`` 又 import
# ``src.application.workspace``。一旦 __init__ 里也 import 它, 就形成了
# ``application -> bootstrap -> api -> application.workspace`` 的环, 直接
# 表现为 "cannot import name ... from partially initialized module"。
# 需要装配时请显式 ``from src.application.bootstrap import build_runtime``,
# 并由 tests/test_bootstrap.py 里的子进程断言守住这条规则。

__all__ = [
    "AppService",
    "AppConfig",
    "ConfigSource",
    "load_config",
    "configure_logging",
    "get_logger",
    "log_event",
    "CourseService",
    "MaterialService",
    "KnowledgeService",
    "ReviewService",
    "LearningService",
    "LearningViewService",
    "normalize_language",
    "MaterialWorkflowService",
    "ClassroomProcessingService",
    "ProcessingJob",
    "JOB_STATUSES",
    "LongAudioASRProvider",
    "QualityCheckedASRProvider",
    "DataLayout",
    "ensure_data_layout",
    "ERROR_CODES",
    "ApplicationError",
    "ConfigurationError",
    "ConflictError",
    "InternalError",
    "InvalidInputError",
    "NotFoundError",
    "ProcessingError",
    "StorageError",
    "UnsupportedError",
    "map_application_error",
]
