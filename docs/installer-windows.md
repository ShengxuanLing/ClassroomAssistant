# Windows 安装包使用说明（TASK-110）

适用版本：**1.1.1**（产物：`ClassroomAssistant-1.1.1-Setup.exe`，105.3 MB，
Inno Setup / LZMA2 压缩，源码来自 PyInstaller onedir 产物 370.7 MB）。

## 下载与安装

1. 从 Release 页面下载 `ClassroomAssistant-<版本>-Setup.exe`。
2. 双击运行。**不需要管理员权限**：装到当前用户目录，不弹 UAC。
3. 第一次运行会弹 SmartScreen 警告 —— 安装包未做代码签名，属预期行为：
   点「更多信息 → 仍要运行」。
4. 安装位置与开始菜单：

   | 项 | 位置 |
   | --- | --- |
   | 程序目录 | `%LOCALAPPDATA%\Programs\ClassroomAssistant` |
   | 开始菜单 | 「课堂助手」文件夹（可选桌面图标，安装时勾选） |
   | 卸载入口 | 设置 → 应用 →「课堂助手 (Classroom Assistant)」，或程序目录下的 `unins000.exe` |

## 启动与托盘

开始菜单启动后：**托盘图标常驻 + 界面自动打开**。默认用 Edge 的 `--app` 模式开一个
**无地址栏、无标签栏的独立窗口**（任务栏里是独立条目，观感与桌面应用一致）；找不到
Edge / Chrome 时回退系统默认浏览器。想强制用浏览器标签页，设
`CLASSROOM_UI_SHELL=browser`（见 [configuration.md](configuration.md) §3）。

托盘右键菜单：

- **打开界面**（双击图标同效）：重新打开浏览器指向运行中的界面；
- **打开数据目录**：在资源管理器里打开 `classroom-data`；
- **退出**：停服务（清理 `.run` 标记）并结束托盘。

双击第二次启动**不会**起第二个实例：端口已在本进程手中且握手确认是同一应用时，
直接把已有界面打开后退出。端口被别的程序占用时才会如实报错（错误码 3）。

## 数据位置

安装版的数据固定放在：

```text
%LOCALAPPDATA%\ClassroomAssistant\classroom-data\
├── materials/  audio/  images/  documents/
├── database/classroom.sqlite    （WAL 模式）
├── logs/classroom.log           （启动、OCR/ASR、托盘退出都有记录）
├── backups/  temp/
└── .run/launcher.pid / launcher.port   （运行期标记，退出时清理）
```

与安装目录分离是**有意设计**：卸载**不会**删除任何用户数据。

## 首次使用与 Whisper 模型

- 图片 OCR 引擎（rapidocr-onnxruntime）随安装包一起分发，**开箱即用、离线**；
- 音频转写（faster-whisper）的语音模型**不在安装包里**：第一次处理音频时
  从 Hugging Face 下载到 `~/.cache/huggingface`（即
  `C:\Users\<你>\.cache\huggingface`）。下载需要网络，之后离线可用。
  这一步失败不影响其他功能。

## 卸载

设置 → 应用里卸载，或静默卸载：

```bat
"%LOCALAPPDATA%\Programs\ClassroomAssistant\unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
```

卸载会清掉程序目录与开始菜单项；`%LOCALAPPDATA%\ClassroomAssistant\classroom-data`
（含数据库与日志）**原样保留**。需要彻底清除时手动删该目录即可。

## 从源码构建安装包（维护者）

前置条件（本机实测环境）：

- 打包 Python：`C:\Users\<你>\.workbuddy-ai\binaries\python\envs\clases-pack`
  （Python 3.13.14 + PyInstaller 6.22.3 + 全部运行时依赖 + pystray/Pillow）；
- Inno Setup 7：`D:\Program Files\Inno Setup 7\ISCC.exe`；
- 一条命令完成 EXE + 安装包：

```bash
python packaging/build.py                 # 读 APPLICATION_VERSION, 先 PyInstaller 后 ISCC
python packaging/build.py --skip-installer   # 只出 dist/ClassroomAssistant/
python packaging/build.py --skip-exe         # 只重打安装包（复用已有 dist/ClassroomAssistant）
```

版本号的唯一真源是 `src/application/workspace.py::APPLICATION_VERSION`；
`packaging/installer.iss` 里 `#ifndef AppVersion` 的回退值被
`tests/test_packaging.py` 钉住，漂移会红。

产物与自检：

| 产物 | 路径 |
| --- | --- |
| onedir | `dist\ClassroomAssistant\ClassroomAssistant.exe` |
| 安装包 | `dist\installer\ClassroomAssistant-<版本>-Setup.exe` |

```bat
:: 冻结产物自检（窗口程序没有控制台, 探针结果写 --out 文件）
dist\ClassroomAssistant\ClassroomAssistant.exe verify-static --out static.txt
dist\ClassroomAssistant\ClassroomAssistant.exe verify-ocr    --out ocr.txt
```

`verify-ocr` 会画一张含 `Classroom / 12345 / Hello` 的图并走真实 rapidocr 识别；
本机实测 `confidence 0.820281`、文本逐字一致。

## 自动化验收挂点（给脚本用）

- 托盘窗口是顶层窗口，类名前缀为 `classroom-assistant`（后缀含
  `SystemTrayIcon`）；可用 `EnumWindows` + `GetClassNameW` 找到；
- 向该窗口 `PostMessageW(hwnd, 0x40A, 0, 0)`（`WM_STOP = WM_USER + 10`）
  即为**干净的退出**：等价于托盘菜单「退出」，`.run` 标记与日志按正常路径处理；
- 探针命令见上；健康检查 `GET /api/health` 的 `storage.data_dir` 可确认数据目录；
- 完整端到端链条（安装 → 启动 → 真实 OCR → 卸载 → 数据保留）的逐项实测输出
  记录在 `docs/status.md` 的 TASK-110 区块（29 项断言全过）。
