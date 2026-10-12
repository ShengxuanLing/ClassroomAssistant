# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")
p = "src/application/workspace.py"
t = open(p, encoding="utf-8").read()
old = (
    "    def _translate_kp_once(\n"
    "        self,\n"
    "        course_id: str,\n"
    "        kp: Mapping[str, Any],\n"
    "        *,\n"
    "        pipeline: Any,\n"
    "        content_language: Optional[str] = None,\n"
    "        force: bool = False,\n"
    "        timeout_seconds: int = 60,\n"
    "    ) -> dict[str, Any]:"
)
new = (
    "    def _translate_kp_once(\n"
    "        self,\n"
    "        course_id: str,\n"
    "        kp: Mapping[str, Any],\n"
    "        *,\n"
    "        pipeline: Any,\n"
    "        content_language: Optional[str] = None,\n"
    "        force: bool = False,\n"
    "        timeout_seconds: Optional[int] = None,\n"
    "    ) -> dict[str, Any]:\n"
    "        # TASK-82: 与 AIAnalysisService.analyze 同一口径 —— 未显式传入时读\n"
    "        # ``load_ai_config().timeout_seconds``, 显式参数可覆盖。\n"
    "        if timeout_seconds is None:\n"
    "            from src.application.ai.config import load_ai_config\n"
    "\n"
    "            timeout_seconds = load_ai_config().timeout_seconds"
)
assert old in t, "workspace.py old block not found"
t = t.replace(old, new, 1)
open(p, "w", encoding="utf-8").write(t)
print("workspace.py OK")

