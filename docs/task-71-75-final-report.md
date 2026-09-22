# TASK 71–75 — REAL-WORLD PILOT & 1.0.1 STABILIZATION

课堂助手 Task 71–75 最终报告：从 1.0.0 进入真实学生使用，修复真实问题，发布 1.0.1。

核心原则（本阶段优先级）：**真实用户问题 > 数据正确性 > 稳定性 > 恢复能力 >
性能 > UX > 新功能**。停止无目的扩展功能，只在发现真实缺陷时修复。

---

## 1. Pilot Environment

- 引入数据目录画像（Task 71.2）：真实使用落在 `classroom-data`（Pilot），测试隔离落在
  `data-test`，二者不相交。
- 数据目录守卫：`ensure_data_layout` 拒绝把仓库根 / `src` / `tests` 当成数据目录，确保
  pytest / stress test / UI audit / backup drill 不会在源码树里就地建库、污染真实 Pilot 数据。
- Pilot 数据（`classroom-data`）与测试数据（`data-test` / `tmp_path`）彻底分离。

## 2. Real Material Tests

- 真实材料格式支持与不支持格式的显式标记见 `docs/task-72-data-quality-report.md`。
- 不支持格式 → `UNSUPPORTED_EXTENSION`（显式失败，不伪造）；缺失 OCR/ASR 运行时 →
  `feature unavailable`（不伪造证据）。

## 3. Real User Flow

- 真实学生闭环（Today → Learning → Exercise → Mistake → Review）在 1.0.0 已串好；
  1.0.1 不新增第二套学习系统，仅硬化错误 UX 与处理可见性。
- 空状态、断点续学、错题/复习工作流由既有套件覆盖
  （`test_learning_workflow.py` 102 / `test_mistakes_center.py` 80 / `test_review_mode.py` 163）。

## 4. Task 71 — Real User Pilot Mode

- **71.2 数据隔离**：✅ 数据目录守卫 + 画像解析（`src/application/data_dirs.py`、
  `src/application/config.py`）。测试：`tests/test_pilot_data_isolation.py`。
- **71.3 首次运行 / 71.4 Onboarding**：沿用 1.0.0 的简洁流程（建课 → 建课节 → 导入 → 处理
  → 看 Knowledge → Review → Today），不暴露 repository / projection / hash / 内部 id。
- **71.5 Error UX**：✅ 用户看到"发生了什么 / 为什么 / 下一步"，绝不暴露
  `KeyError` / `TypeError` / `Traceback` / `SQLiteError`。结构化错误码（`src/application/errors.py`）。
  测试：`tests/test_pilot_error_ux.py`。
- **71.6 Processing Visibility**：✅ 材料状态机 `REGISTERED / VALIDATING / PROCESSING /
  COMPLETED / FAILED`，绝不永久 `loading...`。测试：`tests/test_processing_visibility.py`。
- **71.7 Pilot Logging**：✅ 新增 `src/application/operation_log.py`，记录
  `operation / timestamp / course / session / material / success / duration`，隐私安全
  （密钥整体替换、整段内容 200 字摘要、写入失败绝不抛出、长跑软上限旋转）。
  测试：`tests/test_pilot_operation_log.py`。
- **71.8 Tests**：新增 71.2 / 71.5 / 71.6 / 71.7 专项套件（合计 38 条，含 backend + API）。

## 5. Task 72 — Data Quality & Ingestion Hardening

- 真实材料用例、多语言保留、OCR/Audio 缺失、断裂源、重复处理见
  `docs/task-72-data-quality-report.md`。
- 新增 `src/application/data_quality.py::collect_data_quality`，统计
  `materials processed/failed`、`evidence`、`knowledge`、`unverified`、`conflicted`、
  `broken traces`、`duplicates`、`unsupported formats`。
- 测试：`tests/test_data_quality.py`。

## 6. Task 73 — Real Learning UX Hardening

- 真实学生流、断点续学、错题/复习工作流、空状态、窄屏可用：沿用 1.0.0 的只读派生视图与
  单文件前端（零构建）。新增错误 UX 与处理可见性测试间接覆盖。
- 空状态不得出现 `undefined` / `NaN` / `null` / `loading...`（由 `scripts/ui_render_check.js`
  与既有渲染套件守护）。

## 7. Task 74 — Stability / Recovery / Performance

- 增量处理、崩溃恢复、备份旋转、性能回归、SQL 计数、长跑：见
  `docs/task-74-stability-report.md`。本轮增量改动（数据守卫 + 操作日志）已复跑核心套件确认无回归。

## 8. Task 75 — Release 1.0.1

- **75.1 版本 bump**：因为存在真实用户影响的修复（数据隔离守卫、Pilot 操作日志、数据质量报告）
  而 bump 至 **1.0.1**。版本在 5 处一致：`src/__init__.py`、`src/application/workspace.py`、
  `tests/__init__.py`、`tests/test_production_gate.py`、`tests/test_release_gate.py`。
- **75.2 范围**：仅含真实 bug 修复 / 稳定性 / UX / 数据正确性 / 性能 / 文档修复。无大型新功能。
- **75.3 Regression**：`pytest` + `python -m compileall -q src tests` + 全套门禁（生产 / 重启 /
  备份 / UI audit / UI render / 71–74 测试）。
- **75.4 最终安全审计**：密钥脱敏（操作日志 + 配置层）、路径穿越拒绝（`safe_join`）、静态目录
  不被 `..` 穿越、服务仅监听回环地址、材料收进 data_dir、依赖扫描 0 命中 LLM/云/embedding。
- **75.5 最终 i18n 审计**：`zh == es == ca`（各 370 条，逐键对齐），无缺译 / 裸 key /
  误翻中文 / English fallback / 插值断裂（`scripts/ui_audit.js` 守护）。
- **75.6 数据完整性审计**：`PRAGMA integrity_check` + 12 类实体关系完整（`tests/test_persistence_*.py`）。
- **75.7 最终溯源审计**：抽查 `Knowledge → Evidence → Material → Source`，源已删除则报
  `broken trace`，不伪造恢复（`count_broken_traces` + `tests/test_hardening_traceability.py`）。
- **75.8 隔离审计**：≥5 课 / ≥2 学生，课程 / 学生 / 练习 / 错题 / 复习 / 学习状态隔离
  （`tests/test_multi_course.py` 136 + `tests/test_stress_semester.py::TestCrossCourseLeakage` 9）。
- **75.9 真实用户验收**：完整真人式链路（建课 → 建课节 → 导入 → 处理 → 复核 → Today → 学 →
  练习 → 作答 → 评价 → 错题 → 复核 → 重启 → 续学 → 备份 → 恢复 → 续学）由
  `tests/test_acceptance.py`（105）+ `tests/test_restart_recovery.py` + `tests/test_backup_recovery.py`
  覆盖。

---

## 9. Bugs Found

- 未发现伪造证据 / 静默吞掉不支持格式 / 把不支持文件假装成功等问题（摄取层已合规）。
- 发现并修复：测试套件存在 `Workspace(".")` 在仓库根就地建库的隐患 —— 由数据目录守卫消除。

## 10. Bugs Fixed

- **数据污染隐患**：`tests/test_api_server.py` 的 `Workspace(".")` 会在项目根就地创建
  `database/` `materials/` 等目录，污染源码树 / 真实 Pilot 数据。修复为 `tmp_path` 隔离，并加
  守卫使任何代码路径都无法把仓库根当数据目录。

## 11. UX Changes

- 用户可见错误统一为结构化信封（code + message），不暴露内部栈（Task 71.5）。
- 材料处理状态明确五态，绝不永久 `loading...`（Task 71.6）。

## 12. Data Quality

- 新增 `collect_data_quality` 与报告文档（Task 72.7）。
- 溯源断链显式统计（`broken traces`）。

## 13. Traceability

- 知识点 → 证据 → 材料 → 源的链路由 `Knowledge → Evidence → Material → Source` 审计守护；
  源删除显示 `broken trace`。

## 14. Isolation

- 数据隔离：Pilot（`classroom-data`）/ 测试（`data-test` / `tmp_path`）彻底分离。
- 课程 / 学生 / 练习 / 错题 / 复习 / 学习状态隔离由 `tests/test_multi_course.py` 等守护。

## 15. Performance

- 无新回归；Task 69 已修的 N+1 / write amplification 由 stress 套件恒定 SQL 条数守护。

## 16. Restart

- 10 次重启闭环、指纹/计数/答案历史逐次不变（`tests/test_restart_recovery.py`）。

## 17. Backup

- 3 备 3 恢，恢复到另一 `data_dir`，材料逐字节一致（`tests/test_backup_recovery.py`）。

## 18. Restore

- restore 不破坏当前应用，指纹三轮不变。

## 19. Security

- 无密钥字面量、服务仅回环、静态目录 `..` 穿越拒绝、材料收进 data_dir、依赖 0 命中。

## 20. i18n

- `zh == es == ca` 逐键对齐（各 370 条），无缺译 / 裸 key / 误翻中文。

## 21. Dependency Audit

- 禁止 LLM / 云 API / embedding / 向量库；`torch` 仅 CUDA 分支惰性 import（不参与推理）。

## 22. Known Limitations

1. 绝对耗时不做断言（本机抖动大）；性能门禁断言复杂度，不是毫秒。
2. 10 个 integration 测试（真实 Whisper / OCR 模型未下载）skip，与 1.0.0 一致。
3. 注册期拒绝的不支持格式不进入持久化注册表，因此持久化视图 `unsupported_formats` 为 0；
   unsupported 由摄取响应显式上报。
4. 完整非 integration 回归已在本机执行：**5148 passed, 5 skipped, 0 failed**（约 20:43），权威结果见 `logs/regression-archive/pytest_101_final.txt`。

## 23. Release Changes

- 版本 **1.0.0 → 1.0.1**。
- 新增 `src/application/operation_log.py`（Pilot 操作日志）。
- 新增 `src/application/data_quality.py`（数据质量统计）。
- 强化 `src/application/data_dirs.py` / `config.py`（数据目录守卫 + 画像）。
- 强化 `src/application/workspace.py`（操作日志集成 + 数据守卫）。
- 新增 5 个测试文件（合计 38 条）。
- 新增 `docs/task-72-data-quality-report.md`、`docs/task-74-stability-report.md`、
  `docs/task-71-75-final-report.md`。
- 修复 `tests/test_api_server.py` 的仓库根污染隐患。

## 24. Final Verdict

| Gate | Result |
|---|---|
| Fresh Install | PASS |
| Human Review | PASS |
| Student Learning | PASS |
| Mistake & Weak Knowledge | PASS |
| Multi-course Isolation | PASS |
| Truth Safety | PASS |
| Data Isolation (Pilot/Test) | PASS |
| Persistence | PASS |
| Restart | PASS |
| Backup & Restore | PASS |
| i18n (zh / es / ca) | PASS |
| UI Audit | PASS |
| UI Render Audit | PASS |
| API Contract Gate | PASS |
| Security Gate | PASS |
| Dependency Gate | PASS |
| Test Stability | PASS |
| Full Regression (`-m "not integration"`) | PASS（5148 passed, 5 skipped, 0 failed，全量见 `logs/regression-archive/pytest_101_final.txt`） |
| Compileall | PASS |
| Pilot Operation Log | PASS |
| Data Quality Report | PASS |

```text
FINAL VERDICT: PASS
RELEASE: 1.0.1
```

---

*Task 71–75 完成后，Classroom Assistant 进入：真实使用 → 收集问题 → 小版本修复 → 真实课堂反馈
→ 再决定是否开发 1.1 功能。原则上停止连续大规模开发。*
