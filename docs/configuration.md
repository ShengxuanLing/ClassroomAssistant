# 配置

所有配置都在 `src/application/config.py` 的 `AppConfig` 里，**校验只有一处**
（`AppConfig.validate`）。CLI 不做任何自己的校验——否则「CLI 说合法、配置文件说
不合法」这种错位迟早会出现。

---

## 1. 优先级

```text
命令行 (--xxx)  >  环境变量 (CLASSROOM_XXX)  >  配置文件 (JSON)  >  默认值
```

想确认**最终生效**的配置以及每个值来自哪一层：

```bash
python -m src.application.cli --print-config
python -m src.application.cli --print-config --json
```

`--print-config` **不打开数据库、不绑定端口**，所以数据库坏了也能用它排查。

## 2. 配置文件

默认在 `data_dir` 下查找 `classroom-assistant.json`。用 `--config PATH` 或
`CLASSROOM_CONFIG=PATH` 显式指定时，文件**必须存在**，否则报错（不会静默回落到默认值）。

```json
{
  "data_dir": "D:/classroom-data",
  "host": "127.0.0.1",
  "port": 8765,
  "max_upload_size": 209715200,
  "whisper_model": "base",
  "whisper_device": "cpu",
  "whisper_compute_type": "int8",
  "whisper_language": null,
  "asr_mode": "auto",
  "ocr_config": { "kind": "auto" },
  "log_level": "INFO",
  "debug": false,
  "allow_remote": false
}
```

`database_path` **故意不在这个列表里**：它由 `data_dir` 派生。

## 3. 全部配置项

| 配置键 | 环境变量 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `data_dir` | `CLASSROOM_DATA_DIR` | `<cwd>/classroom-data` | 数据根目录 |
| `database_path` | `CLASSROOM_DATABASE_PATH` | `<data_dir>/database/classroom.sqlite` | SQLite 路径，**必须位于 `data_dir` 内** |
| `host` | `CLASSROOM_HOST` | `127.0.0.1` | 监听地址 |
| `port` | `CLASSROOM_PORT` | `8765` | 监听端口，`0` = 由系统分配 |
| `max_upload_size` | `CLASSROOM_MAX_UPLOAD_SIZE` | `209715200`（200 MiB） | 单次上传字节上限 |
| `whisper_model` | `CLASSROOM_WHISPER_MODEL` | `base` | `tiny` / `base` / `small` / `medium` / `large-v1` / `large-v2` / `large-v3` / `distil-large-v3` |
| `whisper_device` | `CLASSROOM_WHISPER_DEVICE` | `cpu` | `cpu` 或 `cuda` |
| `whisper_compute_type` | `CLASSROOM_WHISPER_COMPUTE_TYPE` | `int8` | `int8` / `float16` / `float32` / `int8_float16` |
| `whisper_language` | `CLASSROOM_WHISPER_LANGUAGE` | `null`（自动识别） | ISO 639-1/639-2 语言码 |
| `asr_mode` | `CLASSROOM_ASR_MODE` | `auto` | `auto` / `real` / `mock` |
| `ocr_config` | `CLASSROOM_OCR_CONFIG` | `{"kind": "auto"}` | JSON 对象，`kind` 取 `auto` / `local` / `mock` |
| `log_level` | `CLASSROOM_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `debug` | `CLASSROOM_DEBUG` | `false` | 是否在错误响应里暴露 debug 细节 |
| `allow_remote` | `CLASSROOM_ALLOW_REMOTE` | `false` | 允许绑定非回环地址 |

### 未识别的环境变量

出现未知的 `CLASSROOM_*` **不会报错**，但会被记入诊断信息。这样拼错
`CLASSROOM_PROT` 就不会再悄无声息——你会在 `--print-config` 的输出里看到它。

### `--no-debug` 与「没给这个开关」的区别

所有 CLI 覆盖项的默认值都是 `None`，而不是 `False`。这样「没给这个开关」与
「显式给了 `--no-debug`」才能区分开；否则 `store_true` 的默认 `False` 会把配置
文件里的 `debug=true` 悄悄压掉。

## 4. 校验规则

`AppConfig.validate()` 会检查：

- **`data_dir`**：非空、不能是文件、**不能落在项目源码树的 `src/` 或 `tests/` 下**。
  后者是 AGENTS.md「用户数据绝不写入 `src/` 或 `tests/`」的可执行版本——否则一次
  手滑 `--data-dir` 就可能把测试产物写进源码树。
- **`database_path`**：非空，且**必须位于 `data_dir` 内**。
- **`host`**：非空；非回环地址（`127.0.0.1` / `localhost` / `::1` 之外）
  必须显式 `allow_remote=True`，否则报错。
- **`port`**：整数，合法范围（`0` 允许，表示由系统分配）。
- **`max_upload_size`**：整数，且不低于 1 KiB——低于这个数几乎必然是「把 MB 当成
  了字节」。
- **`whisper_*`**：模型名、设备、计算精度都在允许集合内。
- **`asr_mode`** / **`ocr_config.kind`** / **`log_level`**：在允许集合内。

## 5. 数据目录

`data_dir` 下固定 8 个子目录（顺序固定，便于文档与测试断言）：

```text
classroom-data/
├── materials/    材料注册表（每个课程一个 JSON）
├── audio/        音频材料副本
├── images/       图片材料副本
├── documents/    文档 / 笔记材料副本
├── database/     classroom.sqlite（+ -wal / -shm 边车文件）
├── logs/         运行日志
├── backups/      备份归档（.zip）
└── temp/         处理中间产物（可安全删除）
```

材料分类由**扩展名唯一决定**：

| 分类 | 落盘目录 |
| --- | --- |
| `note` | `documents/` |
| `document` | `documents/` |
| `audio` | `audio/` |
| `image` | `images/` |

上传的文件名**保持原样**（含中文、重音字符、空格、Windows 非法字符也会被安全
处理），不会被重命名成哈希。

## 6. 安全边界

### 只监听回环地址

默认 `127.0.0.1`。这是**刻意的默认值**，不是省事：本服务没有账号体系、没有鉴权、
没有速率限制，暴露到网络上等于把课堂录音和笔记公开。

要绑到 `0.0.0.0` 之类的地址，必须**显式**：

```bash
python -m src.application.launcher start --host 0.0.0.0 --allow-remote
```

### 密钥识别与脱敏

「什么算 secret」这份定义只放在一处（`src/application/config.py`），否则会出现
「配置层认得 `api_key` 但日志层不认得」这种半吊子脱敏。

判定规则：

- 单个词就足够判定：`password` / `passwd` / `pwd` / `secret` / `secrets` /
  `token` / `tokens` / `credential` / `credentials` / `authorization` / `apikey`
- 需要相邻两个词才判定（避免 `cache_key` 被误判）：
  `api key` / `access key` / `private key` / `session key` / `signing key` /
  `secret key`
- 键名会先按驼峰与连续大写分词，所以 `x-api-key` / `apiKey` / `API_KEY`
  都能识别。

脱敏时**保留键名、只替换值**为 `<redacted>`，便于定位问题。

文本里的密钥形状也会被脱敏：

| 形状 | 例子 |
| --- | --- |
| `key = value` | `api_key=...`、`token: ...`、`password = ...` |
| Authorization 头 | `Authorization: Bearer ...` |
| OpenAI 风格 | `sk-...` |
| JWT | `eyJ...eyJ...` |

## 7. 日志

`log_level` 取 5 个标准级别之一。日志写在 `data_dir/logs/` 下。

控制台编码可能是 GBK / cp1252，而 `data_dir` 路径里可能含西语/加泰语字符。
输出走统一的 `_emit`，编码不了就降级替换——**绝不因为一条路径里有重音字符就让
整个程序启动失败**。

## 8. AI 语义分析配置（TASK-76，可选，默认关闭）

AI 管线配置独立于 `AppConfig`（`src/application/ai/config.py::AIConfig`），由
`build_runtime` 在启动时读取；关闭时旧确定性链路照常工作。

| 环境变量 | 含义 | 默认 |
| --- | --- | --- |
| `CLASSROOM_AI_ENABLED` | `true` 开启 AI 管线 | `false` |
| `CLASSROOM_AI_BASE_URL` | OpenAI-compatible API 地址（仅 https；未配时回落 `CLASSROOM_LLM_API_BASE`） | — |
| `CLASSROOM_AI_API_KEY` | API Key（**只读进程环境**；未配时回落 `CLASSROOM_LLM_API_KEY`；文档/日志/测试一律写 `<YOUR_API_KEY>` 占位） | — |
| `CLASSROOM_AI_MODEL` / `CLASSROOM_AI_TEXT_MODEL` | 主文本模型（回落 `CLASSROOM_LLM_MODEL`） | — |
| `CLASSROOM_AI_VISION_MODEL` | 视觉模型（未配时回落主模型；图片默认走 OCR 文本，不发送图片字节） | 回落主模型 |
| `CLASSROOM_AI_AUDIO_MODEL` | 音频模型（未配时回落主模型；转写默认走本地 Whisper） | 回落主模型 |
| `CLASSROOM_AI_TIMEOUT_SECONDS` | 单次请求超时 | `60` |
| `CLASSROOM_AI_MAX_RETRIES` | 失败重试次数 | `1` |
| `CLASSROOM_AI_JSON_MODE` | 是否发送 `response_format: json_object`（默认 `true`；某些网关对该字段犯病时会 HTTP 200 包错，此时设 `false`，走纯文本 + 严格 schema 校验；非法值直接报错） | `true` |
| `CLASSROOM_AI_ALLOW_IMAGE_BYTES` | 是否把图片原始字节发给模型走 vision（默认 `false`，只发 OCR 文字；`true` 时图片材料 OCR + 原图一起发送，字节出境，上限 8MB/张，超限/未知格式自动回 OCR；vision 失败记 chunk 失败，绝不静默降级） | `false` |
| `CLASSROOM_AI_LIVE_TEST` | `true` 且凭证齐全时才跑真实 API 冒烟（`tests/test_live_ai_smoke.py`），否则干净 SKIP | 未设置 |

### 本地启动环境

启动链统一按下面优先级注入，**三种入口完全相同**：

1. 启动进程已经存在的环境变量；
2. 仓库根目录 `.env`；
3. 兼容旧配置的 `scripts/ai-env.bat`（仅作为缺省补齐）。

因此 `scripts\start.bat`、IDE / PyCharm 直接运行
`src.application.launcher start`、以及 `python -m src.application.cli --check`
不会出现“同一份配置在不同入口生效不同”的情况。加载器只认
`CLASSROOM_AI_*` / `CLASSROOM_LLM_*`，不会借本地辅助文件迁移数据目录；它不执行
shell、变量展开或命令替换，也不打印解析值。

```dotenv
# .env（已被 .gitignore 排除）
CLASSROOM_AI_ENABLED=true
CLASSROOM_AI_BASE_URL=https://<redacted-host>/v1
CLASSROOM_AI_API_KEY=<YOUR_API_KEY>
CLASSROOM_AI_MODEL=<YOUR_MODEL>
```

密钥规则：Key 只经进程环境注入 provider；`repr` / `to_dict` / exception /
启动日志 / 操作日志 / HTTP 响应里只有“有/无”两种形状。`--print-config` 同样
不会打印 Key。真实 API 冒烟只在 `CLASSROOM_AI_LIVE_TEST=true` 且有 Key 时执行，
否则干净 SKIP。

### 运行模式与可观测性

`build_runtime` 与 `GET /api/health` 使用同一口径，公开字段不带凭证：

```json
{
  "ai_mode": "disabled",
  "ai_enabled": false,
  "ai": { "mode": "disabled", "enabled": false }
}
```

- `disabled`：未启用；材料页完整分析返回 `SKIPPED`，不会冒充 AI 已完成；
- `fake`：已开启但无可用凭证，使用确定性 fixture，界面明确标为非真实模型；
- `real`：真实模型。

`process_material` 摄取成功后自动分析，AI 失败只记作业 `ai` 子对象（材料仍
READY，可重试）。成功报告另落盘
`materials/ai-reports/<course>/<material>.json`（衍生缓存，零 DB migration，
重启后总结仍在）。若 AI 正常结束但产出 0 个知识点，统一分析状态会明确返回
`knowledge_point_count: 0` 和 `next_action: inspect_evidence_and_retry`，不会只给一个
无解释的“成功”。

## 9. 退出码

| 码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `2` | 配置错误（`EXIT_CONFIG_ERROR`） |
| `3` | 启动错误（`EXIT_STARTUP_ERROR`，含端口被占用、`stop` 拒绝杀陌生 PID） |
| `4` | 运行期错误（`EXIT_RUNTIME_ERROR`） |

脚本里可以直接用 `%errorlevel%` / `$?` 判断。
