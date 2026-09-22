# -*- coding: utf-8 -*-
"""HTTP endpoint 实现 (Task 38)。

路由表按 spec 的 endpoint 列表设计。课程级资源通过 ``course_id``
query 参数定位 (与 spec 的扁平 endpoint 一致), 因此同一套路由既能被
浏览器 UI 使用, 也能被脚本直接调用。

本层只做: 参数提取 -> 调用应用服务 -> 包装响应。**不包含任何业务规则**,
也绝不直接触碰 domain 对象内部结构 (spec 原则 7)。
"""

from __future__ import annotations

import hashlib
import os
from typing import Any, Callable, Mapping, Optional

from src.application.data_dirs import (
    atomic_write_bytes,
    contains_traversal,
    safe_join,
    sanitize_filename,
)
from src.application.errors import (
    ERROR_CODES,
    ApplicationError,
    InvalidInputError,
    NotFoundError,
    UnsupportedError,
)
from src.application.workspace import Workspace
from src.api.responses import ApiResponse, failure, success
from src.api.router import Request, Router, parse_multipart_file

__all__ = ["build_router", "MAX_UPLOAD_BYTES"]

#: HTTP 层最大上传体积 (与 Workspace 的材料上限保持一致)。
MAX_UPLOAD_BYTES = 200 * 1024 * 1024

#: 材料注册被拒绝时的错误码 -> 结构化错误码。
_REJECTION_CODES: dict[str, str] = {
    "PATH_TRAVERSAL": ERROR_CODES.INVALID_INPUT.value,
    "UNSUPPORTED_EXTENSION": ERROR_CODES.UNSUPPORTED.value,
    "FILE_NOT_FOUND": ERROR_CODES.NOT_FOUND.value,
    "ZERO_BYTE_FILE": ERROR_CODES.INVALID_INPUT.value,
    "OVERSIZED_FILE": ERROR_CODES.INVALID_INPUT.value,
    "EMPTY_PATH": ERROR_CODES.INVALID_INPUT.value,
    "COPY_FAILED": ERROR_CODES.STORAGE_ERROR.value,
}


def build_router(workspace: Workspace) -> Router:
    """把所有 endpoint 绑定到给定工作区。"""
    router = Router()

    # ------------------------------------------------------------------
    # health
    # ------------------------------------------------------------------

    def health(request: Request) -> ApiResponse:
        payload = workspace.health()
        status = 200 if payload["status"] == "ok" else 503
        return success(payload, status=status)

    router.get("/api/health", health)

    # ------------------------------------------------------------------
    # dashboard (Task 39 首页快照; 纯只读投影)
    # ------------------------------------------------------------------

    def dashboard(request: Request) -> ApiResponse:
        return success(workspace.dashboard(request.q("course_id")))

    router.get("/api/dashboard", dashboard)

    # ------------------------------------------------------------------
    # courses
    # ------------------------------------------------------------------

    def list_courses(request: Request) -> ApiResponse:
        return success({"courses": workspace.list_courses()})

    # ---- Task 68: 多课程工作台 (只读投影) ----

    def my_courses(request: Request) -> ApiResponse:
        """全部课程的逐课程总览。

        ``preferred`` 只是"上一秒在看哪门课"的偏好, 只影响 ``selection``
        块的判定, 不影响任何计数 —— 它不是一个查询条件。
        """
        return success(
            workspace.my_courses(
                lang=str(request.q("lang") or "zh"),
                preferred=request.q("preferred"),
            )
        )

    def course_summary(request: Request) -> ApiResponse:
        """单门课的一行总览 (与 ``/api/my-courses`` 里的行同形)。"""
        return success(
            workspace.course_summary(
                request.params["course_id"],
                lang=str(request.q("lang") or "zh"),
            )
        )

    def course_selection(request: Request) -> ApiResponse:
        """把"我想看这门课"解析成"实际该看这门课" (+ 为什么)。"""
        return success(workspace.resolve_course_selection(request.q("preferred")))

    def create_course(request: Request) -> ApiResponse:
        body = request.json_body()
        before = {c["course_id"] for c in workspace.list_courses()}
        dto = workspace.create_course(
            name=body.get("name"),
            code=body.get("code", "") or "",
            language=body.get("language"),
            metadata=body.get("metadata"),
        )
        created = dto["course_id"] not in before
        return success(dto, status=201 if created else 200)

    def get_course(request: Request) -> ApiResponse:
        return success(workspace.get_course(request.params["course_id"]))

    def update_course(request: Request) -> ApiResponse:
        body = request.json_body()
        allowed = {"name", "code", "language", "metadata", "replace_metadata"}
        fields = {k: v for k, v in body.items() if k in allowed}
        if not fields:
            raise InvalidInputError(
                f"no updatable field supplied; allowed: {sorted(allowed)}"
            )
        return success(workspace.update_course(request.params["course_id"], **fields))

    def course_workspace(request: Request) -> ApiResponse:
        """Task 56.1: 课程工作台 (只读投影)。"""
        return success(workspace.course_workspace(request.params["course_id"]))

    def session_workspace(request: Request) -> ApiResponse:
        """Task 56.2/56.3: 课堂工作台 + 推导状态 (只读投影)。

        ``course_id`` 在路径里, 不需要再从 query 传 —— 两个来源会给出
        "以哪个为准"这种无谓问题, 而且不一致时很难解释。
        """
        return success(
            workspace.session_workspace(
                request.params["course_id"], request.params["session_id"]
            )
        )

    router.get("/api/my-courses", my_courses)
    router.get("/api/course-selection", course_selection)
    router.get("/api/courses", list_courses)
    router.post("/api/courses", create_course)
    router.get("/api/courses/{course_id}", get_course)
    router.get("/api/courses/{course_id}/summary", course_summary)
    router.patch("/api/courses/{course_id}", update_course)
    router.get("/api/courses/{course_id}/workspace", course_workspace)
    router.get(
        "/api/courses/{course_id}/sessions/{session_id}/workspace", session_workspace
    )

    # ------------------------------------------------------------------
    # today (Task 56.4)
    # ------------------------------------------------------------------

    def today(request: Request) -> ApiResponse:
        """今日入口: 今天的课程 / 课堂 / 未处理材料 / 待审核 / 待学习。

        "今天" 由 Workspace 的时钟决定, 不接受客户端传日期 —— 否则同一个
        快照在不同浏览器时区下会给出不同的"今天", 那就不是同一份数据了。
        """
        return success(
            workspace.today(
                course_id=request.q("course_id"), student_id=request.q("student_id")
            )
        )

    router.get("/api/today", today)

    def student_today(request: Request) -> ApiResponse:
        """Task 63: 学生视角的今日首页。

        与 ``/api/today`` 的区别: 后者是**课堂侧**今日工作台 (今天的课 /
        未处理材料), 本端点是**学生侧**"今天学什么" —— 学习计划 / 学习路径 /
        待复习 / 待练习 / 最近评估 / 需要注意的知识点。

        ``course_id`` 缺省 = All Courses, 此时每个条目都带 ``course_id``。
        新学生没有任何活动是**正常状态** (``has_activity: false``), 不是错误。
        """
        return success(
            workspace.student_today(
                course_id=request.q("course_id"),
                student_id=request.q("student_id"),
                lang=request.q("lang") or "zh",
            )
        )

    router.get("/api/student-today", student_today)

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------

    def list_sessions(request: Request) -> ApiResponse:
        return success({"sessions": workspace.list_sessions(request.q("course_id"))})

    def create_session(request: Request) -> ApiResponse:
        body = request.json_body()
        course_id = body.get("course_id") or request.q("course_id")
        if not course_id:
            raise InvalidInputError("course_id is required")
        before = {
            s["session_id"] for s in workspace.list_sessions(str(course_id))
        }
        dto = workspace.create_session(
            str(course_id),
            session_number=int(body.get("session_number", 0) or 0),
            date=str(body.get("date", "") or ""),
            title=str(body.get("title", "") or ""),
        )
        created = dto["session_id"] not in before
        return success(dto, status=201 if created else 200)

    def get_session(request: Request) -> ApiResponse:
        return success(workspace.get_session(request.params["session_id"]))

    def process_session(request: Request) -> ApiResponse:
        session_id = request.params["session_id"]
        session = workspace.get_session(session_id)
        report = workspace.process_session(session["course_id"], session_id)
        return success(report)

    router.get("/api/sessions", list_sessions)
    router.post("/api/sessions", create_session)
    router.get("/api/sessions/{session_id}", get_session)
    router.post("/api/sessions/{session_id}/process", process_session)

    # ------------------------------------------------------------------
    # materials
    # ------------------------------------------------------------------

    def list_materials(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        return success(
            {
                "materials": workspace.list_materials(
                    course_id, request.q("session_id")
                )
            }
        )

    def upload_material(request: Request) -> ApiResponse:
        course_id = request.q("course_id")
        session_id = request.q("session_id")
        language = request.q("language")
        filename = request.q("filename") or request.header("x-filename") or ""
        payload = request.body

        content_type = request.header("content-type")
        if content_type.lower().startswith("multipart/form-data"):
            part_name, part_bytes, fields = parse_multipart_file(payload, content_type)
            if part_name is None:
                raise InvalidInputError(
                    "multipart body must contain exactly one file field"
                )
            filename = part_name
            payload = part_bytes
            course_id = fields.get("course_id") or course_id
            session_id = fields.get("session_id") or session_id
            language = fields.get("language") or language

        if not course_id or not str(course_id).strip():
            raise InvalidInputError("course_id is required")
        course_id = str(course_id).strip()
        if not filename:
            raise InvalidInputError(
                "filename is required (query parameter, X-Filename header, "
                "or multipart filename)"
            )
        if not payload:
            raise InvalidInputError("uploaded file is empty")

        # 只取基名, 拒绝任何路径成分 —— 上传方永远无法影响落盘位置。
        safe_name = os.path.basename(str(filename).replace("\\", "/"))
        if not safe_name or contains_traversal(safe_name) or safe_name in (".", ".."):
            return failure(
                ERROR_CODES.INVALID_INPUT.value, "invalid upload filename"
            )

        digest = hashlib.sha256(payload).hexdigest()
        extension = os.path.splitext(safe_name)[1].lower()
        # 暂存目录以内容 hash 命名; 暂存文件名必须**规避本机非法字符**
        # (Windows 上 `<` `>` `:` 等既不能在文件名里出现), 但材料记录的
        # filename 溯源字段仍使用用户原始文件名 —— 见 register_material
        # 的 filename 参数。
        staged_name = sanitize_filename(safe_name, fallback="upload" + extension)
        staged = safe_join(
            workspace.upload_root, digest[:2], digest, staged_name
        )
        atomic_write_bytes(staged, payload)
        try:
            record = workspace.register_material(
                course_id,
                staged,
                session_id=session_id,
                language=language,
                filename=safe_name,
            )
        finally:
            # 暂存副本绝不保留 (受管副本已由工作流原子复制)。
            try:
                os.remove(staged)
            except OSError:
                pass

        if record["processing_status"] == "FAILED":
            rejection = str(record.get("error") or "INVALID_INPUT")
            code = _REJECTION_CODES.get(rejection, ERROR_CODES.INVALID_INPUT.value)
            status = 415 if rejection == "UNSUPPORTED_EXTENSION" else None
            return failure(
                code,
                f"material rejected: {rejection}",
                status=status,
                detail={"error": rejection, "filename": record["filename"]},
            )
        return success(record, status=200 if record.get("duplicate") else 201)

    def get_material(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        return success(workspace.get_material(course_id, request.params["material_id"]))

    # ---- P1/§2: 复习包 (只读装配; LLM 出站仅由这两个显式 GET 触发) ----

    def material_digest(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        return success(
            workspace.material_digest(course_id, request.params["material_id"])
        )

    def course_review_pack(request: Request) -> ApiResponse:
        return success(workspace.course_review_pack(request.params["course_id"]))

    router.get(
        "/api/materials/{material_id}/digest",
        material_digest,
    )
    router.get(
        "/api/courses/{course_id}/review-pack",
        course_review_pack,
    )

    def process_material(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        return success(
            workspace.process_material(course_id, request.params["material_id"])
        )

    def retry_material(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        return success(workspace.retry_material(course_id, request.params["material_id"]))

    def material_evidence(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        return success(
            {
                "evidence": workspace.material_evidence(
                    course_id, request.params["material_id"]
                )
            }
        )

    # ---- TASK-76: AI 语义分析 (显式 POST 触发; GET 只读缓存报告) ----
    #
    # POST /ai-analyze: 对一份已产出 Evidence 的材料执行 AI 理解并落库 KP。
    # 失败是 422 (Evidence 安全、可 retry), 从不是 500。GET /ai-summary:
    # 只读最近一次报告的总结视图, 从不重新调用 AI (§36.5/36.6)。

    def ai_analyze_material(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        body = request.json_body() if request.body else {}
        if not isinstance(body, dict):
            raise InvalidInputError("request body must be a JSON object")
        language = body.get("content_language") or request.q("content_language")
        return success(
            workspace.analyze_material_with_ai(
                course_id,
                request.params["material_id"],
                content_language=str(language).strip() or None if language else None,
            )
        )

    def ai_material_summary(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        return success(
            workspace.ai_summary(course_id, request.params["material_id"])
        )

    router.post("/api/materials/{material_id}/ai-analyze", ai_analyze_material)
    router.get("/api/materials/{material_id}/ai-summary", ai_material_summary)

    router.get("/api/materials", list_materials)
    router.post("/api/materials", upload_material)
    router.get("/api/materials/{material_id}", get_material)
    router.post("/api/materials/{material_id}/process", process_material)
    router.post("/api/materials/{material_id}/retry", retry_material)
    router.get("/api/materials/{material_id}/evidence", material_evidence)

    # ------------------------------------------------------------------
    # processing
    # ------------------------------------------------------------------

    def processing_status(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        return success(workspace.processing_status(course_id, request.q("session_id")))

    def processing_job(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        return success(
            workspace.processing_job(course_id, request.params["material_id"])
        )

    def knowledge_summary(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        return success(workspace.knowledge_summary(course_id))

    router.get("/api/processing", processing_status)
    router.get("/api/processing/{material_id}", processing_job)
    router.get("/api/knowledge-summary", knowledge_summary)

    # ------------------------------------------------------------------
    # timeline (Task 58)
    # ------------------------------------------------------------------

    def timeline(request: Request) -> ApiResponse:
        """Task 58: Unified classroom timeline for a session.

        Returns evidence and knowledge items in chronological order,
        using real timestamps from evidence when available.
        """
        session_id = request.params["session_id"]
        # Get course_id from the session
        session = workspace.get_session(session_id)
        course_id = session.get("course_id") or request.q("course_id")
        from src.application.classroom_view import TimelineFilter, resolve_timeline

        filter_params = request.q("filter")
        filter_obj: Optional[TimelineFilter] = None
        if filter_params:
            # Simple filter parsing: item_types,start_ts,end_ts,knowledge_status,review_status
            parts = filter_params.split(",")
            item_types = parts[0].split(":") if len(parts) > 0 and ":" in parts[0] else None
            start_ts = float(parts[1]) if len(parts) > 1 and parts[1] else None
            end_ts = float(parts[2]) if len(parts) > 2 and parts[2] else None
            k_status = parts[3] if len(parts) > 3 else None
            r_status = parts[4] if len(parts) > 4 else None
            filter_obj = TimelineFilter(
                item_types=item_types,
                start_timestamp=start_ts,
                end_timestamp=end_ts,
                knowledge_status=k_status,
                review_status=r_status,
            )

        items = resolve_timeline(workspace, course_id, session_id, filter=filter_obj)

        return success({"timeline": [item.__dict__ for item in items]})

    router.get("/api/sessions/{session_id}/timeline", timeline)

    # ------------------------------------------------------------------
    # knowledge
    # ------------------------------------------------------------------

    def list_knowledge(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        # Extract filter parameters
        validation_status = request.q("validation_status")
        review_status = request.q("review_status")
        conflict = request.q("conflict")
        language = request.q("language")
        search = request.q("search")
        kps = workspace.knowledge_points(
            course_id,
            session_id=request.q("session_id"),
            topic_id=request.q("topic_id"),
            validation_status=validation_status,
            review_status=review_status,
            conflict=conflict,
            language=language,
            search=search,
        )
        return success({"knowledge_points": kps})

    def get_knowledge(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        return success(workspace.knowledge_point(course_id, request.params["knowledge_id"]))

    def knowledge_evidence(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        return success(
            {
                "evidence": workspace.knowledge_evidence(
                    course_id, request.params["knowledge_id"]
                )
            }
        )

    def knowledge_trace(request: Request) -> ApiResponse:
        """知识点 -> 证据 -> 源材料 的一站式溯源 (Task 39 核心 UX)。"""
        course_id = request.require_q("course_id")
        return success(
            workspace.knowledge_trace(course_id, request.params["knowledge_id"])
        )

    def knowledge_detail(request: Request) -> ApiResponse:
        """Task 59: 知识点详情页。

        显示: 知识点 -> 说明 -> 验证 -> 审核 -> 主题 -> 前置 -> 证据
        """
        course_id = request.require_q("course_id")
        knowledge_id = request.params["knowledge_id"]
        ws = workspace
        # Get knowledge point
        kp = ws.knowledge_point(course_id, knowledge_id)
        # Get evidence
        evidence = ws.knowledge_evidence(course_id, knowledge_id)
        # Get review history
        history = ws.review_history(course_id, knowledge_id)
        # Get topics
        topics: list[dict[str, Any]] = []
        for topic_id in sorted(
            ws.context(course_id).org_service.get_knowledge_point_topics(knowledge_id)
        ):
            topics.append(
                ws.context(course_id).org_service.get_topic(topic_id).to_dict()
            )
        # Get prerequisites (incoming relations = dependencies)
        prerequisites: list[dict[str, Any]] = []
        for rel in ws.context(course_id).org_service.get_related_knowledge(
            knowledge_id, "incoming"
        ):
            prerequisites.append(rel.to_dict())
        # Get session info
        sessions: list[dict[str, Any]] = []
        for session_id in sorted(
            ws.context(course_id).org_service.get_knowledge_point_sessions(knowledge_id)
        ):
            try:
                sessions.append(ws.course_service.get_session(session_id))
            except Exception:
                sessions.append({"session_id": session_id, "missing": True})
        # Get validation and review statuses
        validation_status = kp.get("validation_status")
        review_status = kp.get("review_status")
        # Get evidence trace info
        trace = ws.knowledge_trace(course_id, knowledge_id)
        return success(
            {
                "knowledge": kp,
                "evidence": evidence,
                "review_history": history,
                "topics": topics,
                "prerequisites": prerequisites,
                "sessions": sessions,
                "validation_status": validation_status,
                "review_status": review_status,
                "trace": trace,
            }
        )

    def coverage(request: Request) -> ApiResponse:
        return success(workspace.coverage(request.require_q("course_id")))

    def gaps(request: Request) -> ApiResponse:
        return success(workspace.gaps(request.require_q("course_id")))

    def dependencies(request: Request) -> ApiResponse:
        return success(workspace.dependencies(request.require_q("course_id")))

    def conflicts(request: Request) -> ApiResponse:
        return success({"conflicts": workspace.conflicts(request.require_q("course_id"))})

    def course_knowledge(request: Request) -> ApiResponse:
        return success(workspace.course_knowledge(request.require_q("course_id")))

    router.get("/api/knowledge", list_knowledge)
    router.get("/api/knowledge/{knowledge_id}", get_knowledge)
    router.get("/api/knowledge/{knowledge_id}/evidence", knowledge_evidence)
    router.get("/api/knowledge/{knowledge_id}/trace", knowledge_trace)
    router.get("/api/coverage", coverage)
    router.get("/api/gaps", gaps)
    router.get("/api/dependencies", dependencies)
    router.get("/api/conflicts", conflicts)
    router.get("/api/course-knowledge", course_knowledge)

    # ------------------------------------------------------------------
    # course review center (Task 62)
    # ------------------------------------------------------------------

    def course_review(request: Request) -> ApiResponse:
        """整门课程级复习中心 (只读投影)。

        ``course_id`` 在路径里 —— 与 ``course_workspace`` 一致, 不再要求
        冗余的 query 参数 (两个来源会引出"以哪个为准"这种无谓问题)。
        """
        return success(workspace.course_review(request.params["course_id"]))

    def course_review_summary(request: Request) -> ApiResponse:
        """复习中心摘要 (空课程返回 0 计数, 不是 404)。"""
        return success(
            workspace.course_review_summary(request.params["course_id"])
        )

    router.get("/api/courses/{course_id}/review", course_review)
    router.get(
        "/api/courses/{course_id}/review-summary", course_review_summary
    )

    # ------------------------------------------------------------------
    # review
    # ------------------------------------------------------------------

    def list_reviews(request: Request) -> ApiResponse:
        return success({"reviews": workspace.review_candidates(request.require_q("course_id"))})

    def get_review(request: Request) -> ApiResponse:
        course_id = request.require_q("course_id")
        knowledge_id = request.params["knowledge_id"]
        # 先确认知识点真实存在, 否则"空历史"会被误读成"该点没有问题"。
        workspace.knowledge_point(course_id, knowledge_id)
        history = workspace.review_history(course_id, knowledge_id)
        return success(
            {
                "knowledge_id": knowledge_id,
                "history": history,
                "latest": history[-1] if history else None,
            }
        )

    def review_action(action: str) -> Callable[[Request], ApiResponse]:
        def handler(request: Request) -> ApiResponse:
            course_id = request.require_q("course_id")
            body = request.json_body()
            knowledge_id = request.params["knowledge_id"]
            method = getattr(workspace, f"review_{action}")
            return success(method(course_id, knowledge_id, **body))

        return handler

    router.get("/api/reviews", list_reviews)
    router.get("/api/reviews/{knowledge_id}", get_review)
    router.post("/api/reviews/{knowledge_id}/confirm", review_action("confirm"))
    router.post("/api/reviews/{knowledge_id}/reject", review_action("reject"))
    router.post(
        "/api/reviews/{knowledge_id}/keep-unverified", review_action("keep_unverified")
    )
    router.post(
        "/api/reviews/{knowledge_id}/resolve-conflict",
        review_action("resolve_conflict"),
    )

    # ------------------------------------------------------------------
    # students
    # ------------------------------------------------------------------

    def list_students(request: Request) -> ApiResponse:
        return success({"students": workspace.list_students(request.require_q("course_id"))})

    def create_student(request: Request) -> ApiResponse:
        body = request.json_body()
        course_id = str(body.get("course_id") or request.require_q("course_id"))
        student_id = body.get("student_id")
        if not student_id:
            raise InvalidInputError("student_id is required")
        before = {s["student_id"] for s in workspace.list_students(course_id)}
        dto = workspace.create_student(
            course_id, str(student_id), body.get("display_name")
        )
        return success(dto, status=201 if dto["student_id"] not in before else 200)

    def get_student(request: Request) -> ApiResponse:
        return success(
            workspace.get_student(request.require_q("course_id"), request.params["student_id"])
        )

    def student_state(request: Request) -> ApiResponse:
        return success(
            workspace.student_state(
                request.require_q("course_id"), request.params["student_id"]
            )
        )

    def learning_status(request: Request) -> ApiResponse:
        return success(
            workspace.learning_status(
                request.require_q("course_id"), request.params["student_id"]
            )
        )

    router.get("/api/students", list_students)
    router.post("/api/students", create_student)
    router.get("/api/students/{student_id}", get_student)
    router.get("/api/students/{student_id}/state", student_state)
    router.get("/api/students/{student_id}/learning-status", learning_status)

    # ------------------------------------------------------------------
    # exercises / answers / evaluations
    # ------------------------------------------------------------------

    def list_exercises(request: Request) -> ApiResponse:
        return success({"exercises": workspace.list_exercises(request.require_q("course_id"))})

    def create_exercise(request: Request) -> ApiResponse:
        body = request.json_body()
        course_id = str(body.get("course_id") or request.require_q("course_id"))
        exercise_type = body.get("exercise_type")
        prompt = body.get("prompt")
        kp_ids = body.get("knowledge_point_ids")
        if not exercise_type:
            raise InvalidInputError("exercise_type is required")
        if not prompt:
            raise InvalidInputError("prompt is required")
        if not kp_ids:
            raise InvalidInputError("knowledge_point_ids is required")
        before = {e["exercise_id"] for e in workspace.list_exercises(course_id)}
        extra = {
            k: v
            for k, v in body.items()
            if k
            in (
                "choices", "correct_choice_id", "expected_answer", "is_true",
                "blank_id", "accepted_answers", "evidence_ids", "explanation",
                "difficulty",
            )
        }
        dto = workspace.create_exercise(
            course_id, str(exercise_type), str(prompt), list(kp_ids), **extra
        )
        return success(dto, status=201 if dto["exercise_id"] not in before else 200)

    def get_exercise(request: Request) -> ApiResponse:
        return success(
            workspace.get_exercise(request.require_q("course_id"), request.params["exercise_id"])
        )

    def submit_answer(request: Request) -> ApiResponse:
        body = request.json_body()
        course_id = str(body.get("course_id") or request.require_q("course_id"))
        for field in ("student_id", "exercise_id", "submitted_value"):
            if body.get(field) is None:
                raise InvalidInputError(f"{field} is required")
        dto = workspace.submit_answer(
            course_id,
            str(body["student_id"]),
            str(body["exercise_id"]),
            str(body["submitted_value"]),
            int(body.get("sequence", 0) or 0),
        )
        return success(dto, status=201)

    def get_evaluation(request: Request) -> ApiResponse:
        return success(
            workspace.get_evaluation(
                request.require_q("course_id"), request.params["answer_id"]
            )
        )

    router.get("/api/exercises", list_exercises)
    router.post("/api/exercises", create_exercise)
    router.get("/api/exercises/{exercise_id}", get_exercise)
    router.post("/api/answers", submit_answer)
    router.get("/api/evaluations/{answer_id}", get_evaluation)

    # ------------------------------------------------------------------
    # Task 64: 确定性、有依据的出题工作流
    # ------------------------------------------------------------------

    def _generation_config(body: dict) -> Any:
        """把请求体里的生成参数翻成 ``GenerationConfig``。

        只接受已知字段: 生成配置会进入草稿 id, 静默忽略未知键比报错更糟
        (调用方会以为参数生效了)。
        """
        from src.exercise_generation import GenerationConfig, TemplateId

        known = {"template", "seed", "allow_unverified", "max_options",
                 "distractor_pool", "generator_version"}
        unknown = sorted(set(body) - known)
        if unknown:
            raise InvalidInputError(
                "unknown generation option(s): " + ", ".join(unknown)
            )
        if not body:
            return None
        template = body.get("template")
        if template is not None:
            try:
                template = TemplateId(str(template))
            except ValueError:
                raise InvalidInputError(f"unknown template: {template!r}")
        distractor_pool = body.get("distractor_pool")
        kwargs: dict[str, Any] = {
            "template": template,
            "seed": body.get("seed"),
            "allow_unverified": bool(body.get("allow_unverified", False)),
        }
        if body.get("max_options") is not None:
            kwargs["max_options"] = int(body["max_options"])
        if distractor_pool is not None:
            kwargs["distractor_pool"] = tuple(str(d) for d in distractor_pool)
        if body.get("generator_version"):
            kwargs["generator_version"] = str(body["generator_version"])
        return GenerationConfig(**kwargs)

    def preview_generated_exercise(request: Request) -> ApiResponse:
        body = request.json_body()
        course_id = str(body.get("course_id") or request.require_q("course_id"))
        kp_id = body.get("knowledge_point_id") or request.q("knowledge_point_id")
        if not kp_id:
            raise InvalidInputError("knowledge_point_id is required")
        return success(
            workspace.preview_exercise(
                course_id, str(kp_id), config=_generation_config(
                    {k: v for k, v in body.items()
                     if k not in ("course_id", "knowledge_point_id")}
                )
            )
        )

    def generate_exercise(request: Request) -> ApiResponse:
        body = request.json_body()
        course_id = str(body.get("course_id") or request.require_q("course_id"))
        kp_id = body.get("knowledge_point_id") or request.q("knowledge_point_id")
        if not kp_id:
            raise InvalidInputError("knowledge_point_id is required")
        result = workspace.generate_exercise(
            course_id, str(kp_id), config=_generation_config(
                {k: v for k, v in body.items()
                 if k not in ("course_id", "knowledge_point_id")}
            )
        )
        if not result.get("generated"):
            # 拒绝生成是**正常结果**, 不是错误: 200 + 结构化原因,
            # 让 UI 能显示"请先审核"而不是一个红色错误页。
            return success(result)
        return success(result, status=201 if result.get("created") else 200)

    def generate_exercise_batch(request: Request) -> ApiResponse:
        body = request.json_body()
        course_id = str(body.get("course_id") or request.require_q("course_id"))
        kp_ids = body.get("knowledge_point_ids")
        limit = body.get("limit")
        result = workspace.generate_exercises(
            course_id,
            knowledge_point_ids=list(kp_ids) if kp_ids else None,
            config=_generation_config(
                {k: v for k, v in body.items()
                 if k not in ("course_id", "knowledge_point_ids", "limit")}
            ),
            limit=int(limit) if limit is not None else None,
        )
        return success(result)

    def exercise_grounding(request: Request) -> ApiResponse:
        return success(
            workspace.exercise_grounding(
                request.require_q("course_id"), request.params["exercise_id"]
            )
        )

    def exercise_start(request: Request) -> ApiResponse:
        return success(
            workspace.exercise_start(
                request.require_q("course_id"), request.params["exercise_id"]
            )
        )

    def exercise_submit(request: Request) -> ApiResponse:
        body = request.json_body()
        course_id = str(body.get("course_id") or request.require_q("course_id"))
        for field in ("student_id", "exercise_id", "submitted_value"):
            if body.get(field) is None:
                raise InvalidInputError(f"{field} is required")
        return success(
            workspace.exercise_submit(
                course_id,
                str(body["student_id"]),
                str(body["exercise_id"]),
                str(body["submitted_value"]),
                int(body.get("sequence", 0) or 0),
            ),
            status=201,
        )

    def knowledge_evidence_trace(request: Request) -> ApiResponse:
        return success(
            workspace.knowledge_evidence_trace(
                request.require_q("course_id"),
                request.params["knowledge_id"],
            )
        )

    router.get("/api/exercise-generation/preview", preview_generated_exercise)
    router.post("/api/exercise-generation", generate_exercise)
    router.post("/api/exercise-generation/batch", generate_exercise_batch)
    router.get("/api/exercises/{exercise_id}/grounding", exercise_grounding)
    router.get("/api/exercises/{exercise_id}/start", exercise_start)
    router.post("/api/exercise-workflow/submit", exercise_submit)
    router.get("/api/knowledge/{knowledge_id}/evidence-trace", knowledge_evidence_trace)

    # ------------------------------------------------------------------
    # 错题与薄弱知识点中心 (Task 65)
    # ------------------------------------------------------------------

    def mistakes_center(request: Request) -> ApiResponse:
        # group_by / lang 都只是**展示**选项, 不改变事实。
        group_by = str(request.q("group_by") or "knowledge")
        lang = str(request.q("lang") or "zh")
        return success(
            workspace.mistakes_center(
                request.require_q("course_id"),
                request.params["student_id"],
                group_by=group_by,
                lang=lang,
            )
        )

    def mistake_detail(request: Request) -> ApiResponse:
        return success(
            workspace.mistake_detail(
                request.require_q("course_id"),
                request.params["student_id"],
                request.params["knowledge_id"],
            )
        )

    router.get("/api/students/{student_id}/mistakes", mistakes_center)
    router.get(
        "/api/students/{student_id}/mistakes/{knowledge_id}", mistake_detail
    )

    # ------------------------------------------------------------------
    # study plans / learning paths
    # ------------------------------------------------------------------

    def study_plan(request: Request) -> ApiResponse:
        return success(
            workspace.study_plan(
                request.require_q("course_id"), request.params["student_id"]
            )
        )

    def learning_path(request: Request) -> ApiResponse:
        return success(
            workspace.learning_path(
                request.require_q("course_id"), request.params["knowledge_id"]
            )
        )

    router.get("/api/study-plans/{student_id}", study_plan)
    router.get("/api/learning-paths/{knowledge_id}", learning_path)

    # ------------------------------------------------------------------
    # learning UI (Task 40)
    # ------------------------------------------------------------------

    def student_dashboard(request: Request) -> ApiResponse:
        return success(
            workspace.student_dashboard(
                request.require_q("course_id"), request.params["student_id"]
            )
        )

    def student_learning_path(request: Request) -> ApiResponse:
        return success(
            workspace.student_learning_path(
                request.require_q("course_id"),
                request.params["student_id"],
                request.params["knowledge_id"],
            )
        )

    def knowledge_explanation(request: Request) -> ApiResponse:
        return success(
            workspace.grounded_explanation(
                request.require_q("course_id"),
                request.params["knowledge_id"],
                request.q("language", "es") or "es",
            )
        )

    def record_learning_event(request: Request) -> ApiResponse:
        body = request.json_body()
        knowledge_id = body.get("knowledge_id")
        event_type = body.get("event_type")
        if not knowledge_id:
            raise InvalidInputError("knowledge_id is required")
        if not event_type:
            raise InvalidInputError("event_type is required")
        return success(
            workspace.record_learning_event(
                request.require_q("course_id"),
                request.params["student_id"],
                str(knowledge_id),
                str(event_type),
            )
        )

    # Task 41: Exercise / Answer / Evaluation view (学生视角, 提交前不含答案)
    def student_exercises(request: Request) -> ApiResponse:
        return success(
            workspace.exercise_list_view(
                request.require_q("course_id"), request.params["student_id"]
            )
        )

    def student_exercise_view(request: Request) -> ApiResponse:
        return success(
            workspace.exercise_view(
                request.require_q("course_id"),
                request.params["student_id"],
                request.params["exercise_id"],
            )
        )

    def student_exercise_evaluation(request: Request) -> ApiResponse:
        return success(
            workspace.exercise_evaluation_view(
                request.require_q("course_id"),
                request.params["student_id"],
                request.params["exercise_id"],
            )
        )

    router.get("/api/students/{student_id}/dashboard", student_dashboard)
    router.get(
        "/api/students/{student_id}/learning-paths/{knowledge_id}",
        student_learning_path,
    )
    router.post("/api/students/{student_id}/learning-events", record_learning_event)
    router.get("/api/students/{student_id}/exercises", student_exercises)
    router.get(
        "/api/students/{student_id}/exercises/{exercise_id}",
        student_exercise_view,
    )
    router.get(
        "/api/students/{student_id}/exercises/{exercise_id}/evaluation",
        student_exercise_evaluation,
    )
    router.get(
        "/api/knowledge/{knowledge_id}/explanation", knowledge_explanation
    )

    # ------------------------------------------------------------------
    # 今天的学习流程 (Task 66)
    # ------------------------------------------------------------------
    #
    # 学生视角的连续学习入口。全部经 ``LearningWorkflow`` 读既有 Domain
    # 事实: 学习计划 / 学习路径 / 知识点 / 证据 / 练习 / 作答 / 评估 /
    # 学生状态。本层不新增业务规则。

    def learning_start(request: Request) -> ApiResponse:
        """「开始今天的学习」: 当前学习任务 + 前置 + 进度 + 下一步。"""
        return success(
            workspace.learning_workflow_start(
                request.require_q("course_id"),
                request.params["student_id"],
                lang=str(request.q("lang") or "zh"),
            )
        )

    def learning_knowledge(request: Request) -> ApiResponse:
        """知识学习页: KP / Evidence / Material / Grounded explanation / 状态。"""
        return success(
            workspace.learning_workflow_knowledge(
                request.require_q("course_id"),
                request.params["student_id"],
                request.params["knowledge_id"],
                lang=str(request.q("lang") or "zh"),
                language=request.q("language"),
            )
        )

    def learning_open_knowledge(request: Request) -> ApiResponse:
        """打开知识页 (POST, 因为它会记一个 viewed 事件)。

        ``course_id`` 从 body **或** 查询串取 —— 与 ``learning_answer``
        保持同一契约。只认查询串会让"把参数放 body"的调用方拿到一个
        ``course_id is required`` 的 400, 而同一组里的其它 POST 端点又接受
        body, 调用方无从预测, 属于接口层面的自相矛盾。
        """
        body = request.json_body()
        course_id = str(body.get("course_id") or request.require_q("course_id"))
        return success(
            workspace.learning_workflow_open_knowledge(
                course_id,
                request.params["student_id"],
                request.params["knowledge_id"],
                lang=str(body.get("lang") or request.q("lang") or "zh"),
                language=body.get("language") or request.q("language"),
            )
        )

    def learning_exercise(request: Request) -> ApiResponse:
        """当前知识点的练习 (已有则复用; 否则幂等生成)。"""
        return success(
            workspace.learning_workflow_exercise(
                request.require_q("course_id"),
                request.params["student_id"],
                request.params["knowledge_id"],
            )
        )

    def learning_answer(request: Request) -> ApiResponse:
        """作答 -> 评价 -> 学生状态 -> 下一学习任务。

        ``student_id`` 以路径参数为准（``/api/students/{student_id}/learning/answer``）；
        body 里若也给了就必须一致 —— 不一致说明调用方把学生搞错了，静默采用
        其中一个会让答案写到错误的学生名下。``course_id`` 可以从 body 或
        查询串给（与既有 submit 端点一致）。
        """
        body = request.json_body()
        course_id = str(body.get("course_id") or request.require_q("course_id"))
        path_student = str(request.params.get("student_id") or "").strip()
        body_student = str(body.get("student_id") or "").strip()
        if path_student and body_student and path_student != body_student:
            raise InvalidInputError(
                "student_id in the request body does not match the URL"
            )
        student_id = path_student or body_student
        if not student_id:
            raise InvalidInputError("student_id is required")
        for field in ("exercise_id", "submitted_value"):
            if body.get(field) is None:
                raise InvalidInputError(f"{field} is required")
        return success(
            workspace.learning_workflow_answer(
                course_id,
                student_id,
                str(body["exercise_id"]),
                str(body["submitted_value"]),
                int(body.get("sequence", 0) or 0),
            ),
            status=201,
        )

    router.get(
        "/api/students/{student_id}/learning/start", learning_start
    )
    router.get(
        "/api/students/{student_id}/learning/knowledge/{knowledge_id}",
        learning_knowledge,
    )
    router.post(
        "/api/students/{student_id}/learning/knowledge/{knowledge_id}",
        learning_open_knowledge,
    )
    router.get(
        "/api/students/{student_id}/learning/knowledge/{knowledge_id}/exercise",
        learning_exercise,
    )
    router.post("/api/students/{student_id}/learning/answer", learning_answer)

    # ------------------------------------------------------------------
    # 考前复习模式 (Task 67)
    # ------------------------------------------------------------------
    #
    # 只读。响应的字段集合里**没有**概率 / 预测类字段 —— 见
    # ``src.application.review_mode.NO_PREDICTION_FIELDS``。
    # 这是一条契约, 不只是"我们不去算": ReviewSet 上没有写方法,
    # 因此冲突不可能被自动解决。

    def student_review_set(request: Request) -> ApiResponse:
        return success(
            workspace.student_review_set(
                request.require_q("course_id"),
                request.params["student_id"],
                lang=str(request.q("lang") or "zh"),
            )
        )

    router.get("/api/students/{student_id}/review-set", student_review_set)

    return router
