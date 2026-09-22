# -*- coding: utf-8 -*-
"""AI 语义理解管线 (TASK-76)。

把确定性 ingestion 之后的材料变成结构化知识候选, 再经确定性校验落到
现有 ``KnowledgePoint / Evidence / Review`` 模型上::

    Source Material -> Evidence -> chunk -> AI Provider -> candidates
        -> validation -> evidence grounding -> dedup/merge
        -> KnowledgePoint (+ Review)

硬性边界 (与现有架构一致):

- LLM 永远不是事实来源: 无 Evidence 支撑的候选一律 ``needs_review``,
  高置信度自动接受**必须**有合法 Evidence 绑定。
- LLM 绝不直接写库 / 决定 ID / 改 student state / 解冲突。
- 默认 ``FakeAIProvider`` (确定性 fixture, 零出站); 真实 provider 只用
  标准库 ``urllib`` 访问 OpenAI-compatible ``/chat/completions``,
  不新增任何第三方依赖 (release gate 的 AI 供应链门禁保持全绿)。
- API Key 只读进程环境变量, 绝不进 git / 日志 / UI / 测试 fixture。
- 本包只依赖领域模型与 ``src.application`` 的轻量模块
  (``errors`` / ``config`` 的脱敏原语 / ``runtime`` 时钟), 绝不 import
  ``src.persistence`` / ``src.backup`` / ``src.api`` / ``src.web``
  (分层守卫 ``tests/test_persistence_layering.py``)。
"""

from __future__ import annotations

__all__ = ["PIPELINE_VERSION", "PROMPT_VERSION"]

#: AI 管线版本 (进入 processing identity 与结果版本记录; 换 prompt 或
#: 合并策略时必须同步 bump, 否则幂等键会把新旧结果混为一谈)。
PIPELINE_VERSION = "ai-pipeline-v1"

#: 提示词版本 (与 provider.py / prompts.py 的版本保持一致)。
PROMPT_VERSION = "ai-prompts-v1"
