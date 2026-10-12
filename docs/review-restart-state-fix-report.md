# Review Restart-State 修复报告

日期: 2026-10-12
状态: 已完成 — P0 恢复、P1 回归验证、P2 隔离，最终闭环

---

## 1. 缺陷摘要

同一 `knowledge_id` 在两个不同对象间重复：

- **权威对象**: `Organization._kp_by_id`（由 AI 落库 `workflow.register_knowledge_point` 建立）
- **持久载体**: `processing.structure.knowledge_points`（由 `_mirror_to_structure` 建立）

HTTP confirm 只改权威对象，但 `_flush_knowledge` 只写载体，于是重启后从载体
读回 `pending`，与审阅历史里已经决定的状态冲突。根因不是事务、数据库文件
还是快照错误，而是两个对象副本不同步。

## 2. 修复内容

### A. src/application/workspace.py
`Workspace._flush_knowledge` 调用 `Workspace._reconcile_review_status`：
flush 之前把审核当前状态从权威 org 副本同步进持久化载体。字段级、幂等、
无 SQL，且仍在同一个 `_atomic` 事务内。

### B. src/knowledge_organization.py
`KnowledgeOrganizationService.register_knowledge_point`：同 id 重新登记时，
若现有记录已决议，决策不被覆回 `pending`；决定只能由 `ReviewService` 的显式
决策改变（人工审核决定是粘性的）。

### C. src/knowledge_review.py
`ReviewRecord` 加入 `evidence_fingerprint`（append-only）：重启可见的重开
依据。

### D. src/application/persistence_wiring.py
`WorkspacePersistence.atomic` 改用 `threading.RLock`：`Database._depth` 与
`_write_ordinal` 实例级、被 `ThreadingHTTPServer` 共享，否则并发请求的
savepoint 编号互踩（实测 `no such savepoint: sp_1`）。

### E. 审阅源收敛
`review_candidates` 经 `ReviewService`（唯一审阅记录持有者）——队列语义与
历史共用同一源，指纹重开在队列端点上可达。

## 3. 测试结果

### 修复后回归 1：`tests/test_review_restart_state.py`
**20/20 通过**（重复分析幂等、指纹重开、并发 reviewer、原子性）。

### 修复后回归 2：`tests/test_knowledge_review.py` + `tests/test_review_mode.py`
**200 passed，1 skipped，31.21s**。

### 修复后全量回归（rfull）
**914 passed，1 failed，8 skipped，259.54s**。

单失败项：`tests/test_student_ui.py::TestTheLiveSessionPickerEndToEnd`
`::test_the_live_script_passes`。

- 这是**环境性故障**：外部 Node E2E 脚本试图启动
  `D:\Project\Clases\Python\pythoncore-3.14-64\python.exe`，该文件在本机
  不存在（`ENOENT`），`0.35~0.40s` 内即失败。
- 原始代码复验：同一测试在**未修代码树**上以同样 `0.40s` 的 `ENOENT` 失败
 （stash 后运行）——与本次修复无关，不属于 pytest 等价性门禁（不在
  `test_*.py` 回归集合里，依赖外部 node/python 核心）。

### 变异测试
拆掉 `reconcile` / 注册守卫后 **15/20 红**，恢复代码后 **20/20 绿**。

## 4. 数据完整性

`git status` 显示仅 4 个源文件修改 + 1 个测试文件新增：

```
 M src/application/persistence_wiring.py
 M src/application/workspace.py
 M src/knowledge_organization.py
 M src/knowledge_review.py
?? tests/test_review_restart_state.py
```

无 frozen/official 数据文件被修改；`classroom-data/` 已 gitignored，所有产物
均在 `temp/`（也已忽略）。历史占位目录（`input/ output/ audio/ images/
documents/ materials/ backups/`）的 README 保留。

## 5. 未完成项 / 后续建议

- 外部 E2E（`test_student_ui.py::TestTheLiveSessionPickerEndToEnd` 这一条）
  阻塞于本机 python 核心缺失，需要安装 `Python/pythoncore-3.14-64` 后单独
  验收（文档: `docs/ci.md`）。
- 生产环境再次建议：增加请求级幂等键（P2），区分"重试同一决定"与"并发两条
  新决定"。当前行为按文档 append 式合约执行（并发时不静默丢历史），已被
  并发测试确认。
