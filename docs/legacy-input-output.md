# 旧目录布局（input/ output/）→ 新布局（classroom-data/）对照表

> 状态：**已归档**。本文档只用于解释历史脚本与早期规范，不代表当前行为。
> 当前唯一数据真相源是 `classroom-data/`（见 `AGENTS.md` 的「文件组织规范」章与
> `src/application/data_dirs.py` 的 `DATA_LAYOUT_DIRS`）。

## 背景

项目早期规范（`AGENTS.md` 初始版）设想了 `input/` 与 `output/` 两套目录树，并规定
所有输入材料先落 `input/` 各子目录、所有生成物落 `output/` 各子目录，文件用
`小写 + 连字符` 命名并带 YAML frontmatter。

实线落地时，产品改用**内容寻址 ID + `classroom-data/` 8 目录**作为唯一真相源
（见 `src/application/data_dirs.py`）。截至 2026-09-20，`input/` 与 `output/`
**不含任何业务数据**（各只剩一个占位说明 `README.md`，用于让历史脚本/笔记里的
路径不至于指向不存在的目录），`classroom-data/` 已承担全部受管数据。

因此采用决策 **A**：`classroom-data/` 为唯一真相源，`input/` `output/` 仅留空占位，
新代码禁止写入。

## 对照表

| 旧布局（已废止） | 旧用途 | 新布局（classroom-data/） | 说明 |
|---|---|---|---|
| `input/audio/` | 原始音频文件 | `classroom-data/audio/` | 材料副本，内容寻址 |
| `input/images/` | 板书 / 图片 | `classroom-data/images/` | 材料副本，内容寻址 |
| `input/notes/` | 用户 / 同学笔记 | `classroom-data/documents/` | 笔记 / PDF / DOCX 副本 |
| `input/syllabi/` | 课程大纲 | `classroom-data/documents/` | 与笔记同归文档树 |
| `output/transcripts/` | 音频转录文本 | `classroom-data/database/`（转录作为 Evidence 落库） | 不再落盘为 `.md` |
| `output/ocr/` | OCR 结果 | `classroom-data/database/`（OCR 作为 Evidence 落库） | 不再落盘为 `.md` |
| `output/knowledge/` | 知识点梳理 | `classroom-data/database/`（KnowledgePoint 落库） | 不再落盘为 `.md` |
| `output/review-materials/` | 复习材料 | `classroom-data/database/` + 前端实时投影 | 不再落盘为 `.md` |
| `output/study-plans/` | 复习规划 | `classroom-data/database/`（StudyPlan 落库） | 不再落盘为 `.md` |
| `cache/` | 中间缓存文件 | `classroom-data/temp/` | 临时暂存区 |
| `logs/` | 处理日志 | `classroom-data/logs/` | 运行日志 |

## 命名与格式变化

| 旧规则 | 新规则 |
|---|---|
| 文件名 `小写 + 连字符`（如 `concepto-funcion-explanation.md`） | 受管材料用**内容寻址 ID**；原始文件名通过 `filename=` 逐字保留 |
| 元数据用 YAML frontmatter | 受管材料元数据由数据库 + 内容寻址索引承载，不依赖 frontmatter |
| 每个知识点 / 转录各落一份 `.md` | 统一落 `classroom-data/database/classroom.sqlite`（WAL 模式），导出时才生成可读文件 |

## 给历史脚本的提示

- 若某脚本仍向 `input/` 或 `output/` 写文件，应改为调用 `Workspace.register_material`
  真入口（复制进 `classroom-data/` 对应子树）。
- 若需要把数据**导出**给人工阅读，从数据库 / 索引读取后写成可读文件到调用方指定目录，
  **不要**把 `classroom-data/` 当导出目标读回（导出快照是单向的）。
