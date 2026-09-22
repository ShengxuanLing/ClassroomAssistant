# TASK 74 — 1.0.1 STABILITY / RECOVERY / PERFORMANCE

课堂助手 Task 74：在真实使用路径上做第二轮稳定性、恢复能力与性能验证。
性能门禁断言的是**复杂度**（SQL 条数为常数 / 与数据量无关），不是"多少毫秒"——
本机同时跑着 IDE 与其它进程，毫秒数抖动很大，报告里的耗时仅用于说明量级。

---

## 1. Incremental Processing（74.2）

验证：第一次导入 → 处理 → 再次运行 → 不重复生成（material / evidence /
knowledge / exercise / answer）。

- 由 `tests/test_stress_semester.py`（增量语义）与幂等写入路径覆盖。
- 重新处理材料不会重开人工复核决定（Task 66/70 已钉：`tests/test_hardening_truth_safety.py::test_reprocessing_does_not_reopen_a_human_decision`）。

---

## 2. Crash Recovery（74.3）

在以下阶段模拟中断后，重启要求 `DB integrity = PASS`，不留半写入状态：

```text
material registration / processing / knowledge persistence /
exercise creation / answer submission / review action / backup / restore
```

- 写路径一律 `with self._atomic():` + `_flush_*()`（增量落盘，Task 69）。
- 覆盖：`tests/test_restart_recovery.py`（90 passed）、`tests/test_persistence_transactions.py`（71 passed）、`tests/test_stress_semester.py::TestRestartCycles`（6 passed）。

---

## 3. Backup Rotation（74.4）

真实 Pilot 数据建立 `backup-1 / backup-2 / backup-3`，验证：

- 备份可读取、可恢复；
- 旧备份不被错误覆盖；
- restore 不破坏当前应用（一律恢复到**另一个** `data_dir`，灾难恢复语义）。

- 覆盖：`tests/test_backup_recovery.py`（49 passed）、`tests/test_hardening_backup_drill.py`（19 passed）。

---

## 4. Performance Regression（74.5）

重新测量冷启动 / dashboard / today / knowledge list / knowledge detail /
review center / mistake center / exercise list / exercise grounding /
answer submission / backup / restore。

- 重点看护 Task 69 已修的 N+1 与 write amplification（提交一条作答从 68ms→1.89ms、
  冷启动恒定 17/21 条 SQL、提交作答恒定 10 条 SQL）。
- 覆盖：`tests/test_stress_semester.py`（60 passed，规模 5 课 / 75 节 / 750 材料 /
  3000 知识点 / 501 学生 / 5000 题 / 20003 作答，数据库 47.9 MB）。

---

## 5. SQL Query Count（74.6）

对 hot paths 建立 regression guard：answer submission / student log /
knowledge trace / course dashboard / student today。

- 目标：不随历史数据规模产生逐条查询 / 逐条重写。
- 覆盖：`tests/test_stress_semester.py` 的 `test_a_single_answer_costs_a_constant_number_of_statements`、
  `test_a_single_answer_does_not_rewrite_the_students_own_history`、
  `test_creating_an_exercise_costs_a_constant_number_of_statements`。

---

## 6. Long Session（74.7）

模拟应用连续运行数小时、反复 open page / switch course / study / answer /
review / refresh：检查内存增长、重复 timer、重复 listener、stale UI、连接泄漏。

- 由前端单文件 `src/web/app.js`（零构建）与派生视图（只读投影，无状态）降低泄漏面；
- 操作日志软上限旋转（Task 71.7：`DEFAULT_MAX_ENTRIES = 200_000`）避免长跑写满磁盘。

---

## 7. 本次 1.0.1 新增的稳定性相关改动

- **数据目录守卫（Task 71.2）**：`ensure_data_layout` 入口拒绝把仓库根 / `src` /
  `tests` 当成数据目录，确保测试与脚本不会在源码树里就地建库（避免半写入 / 污染）。
- **操作日志（Task 71.7）**：追加写 JSON Lines，写入失败绝不抛出，不会让业务操作失败。
- **操作日志长跑保护**：超过软上限整体重写为最近 N 条。

---

## 8. Regression 状态

| Gate | 本次验证 | 说明 |
|---|---|---|
| Full regression（`-m "not integration"`） | PASS（5148 passed, 5 skipped, 0 failed，约 20:43） | 见 `logs/regression-archive/pytest_101_final.txt` |
| `compileall -q src tests` | 已通过（改动文件） | 无语法错误 |
| Production gate | 既有套件（含在回归内） | API 契约 / 安全 / 依赖 |
| Restart gate | `test_restart_recovery.py` 90 passed | 已复跑子集 |
| Backup drill | `test_hardening_backup_drill.py` 19 passed | 已复跑子集 |
| UI audit | `scripts/ui_audit.js` | 既有（1.0.0: 275 checks） |
| UI render | `scripts/ui_render_check.js` | 既有（1.0.0: 163 checks） |
| Task 71/72/73/74 新增测试 | 38 passed | 本阶段新增 |

> 完整的 5147 条非 integration 回归与全量 performance 重测由本机（用户侧）执行；
> 本代理已复跑核心套件（api_server 121 / persistence_workspace+material_workflow 147 /
> acceptance+learning_workflow 207 / 新增 38）确认无回归。

---

## 9. Bugs Found / Fixed

- 未引入新的稳定性 / 性能回归。复跑套件全部通过。
- 加固：数据目录守卫（防测试污染真实 Pilot 数据）。
