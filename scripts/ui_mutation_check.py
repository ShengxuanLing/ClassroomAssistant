#!/usr/bin/env python3
"""变异测试: 破坏本轮立下的视觉不变量, 验证 ui_audit.js 的断言**精准**报红。

为什么需要它
------------
`ui_audit.js` 里那些"CSS 必须有某条规则"的断言有一个共同的失效方式:
**写得太宽**, 于是永远为真。真实案例 (2026-10-14): "css gives focus a visible
ring" 写成 `/:focus-visible\\s*\\{[^}]*outline: 2px solid var\\(--accent\\)/`,
而画布那条 `.km-graph canvas:focus-visible` 也满足它 —— 把全局焦点环整条删掉,
审计依然全绿。断言通过不等于断言有效; 只有"故意改坏, 看它有没有红"才能区分。

怎么用
------
    python scripts/ui_mutation_check.py

对每个变异: 备份被改的文件 -> 打补丁 -> 跑 `node scripts/ui_audit.js` ->
无论成败都立刻还原。判据是**两条**:
  1. 审计必须以非零码退出;
  2. 退出原因里必须出现预期的那条断言名 (不是"别的地方碰巧红了")。
另外每条补丁都必须命中恰好 1 处 —— 打偏了会记成异常 (说明变异没生效, 那次
绿灯没有任何意义)。

注意: 它会**临时改写** `src/web/` 下的真实文件, 所以不要在别的检查正在跑的时候
执行。脚本用 try/finally 保证还原, 结束时还会逐文件断言已回到原样。
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
CSS = ROOT / "src" / "web" / "styles.css"
APP = ROOT / "src" / "web" / "app.js"

#: (说明, 目标 "css"/"app", 正则, 替换, 预期报红的断言名片段)
MUTATIONS: list[tuple[str, str, str, str, str]] = [
    ("删掉顶栏当前分区样式", "css",
     r"\.topnav a\.active \{[\s\S]*?\n\}\n", "",
     "css styles the active top-nav item"),
    ("给卡片加回投影", "css",
     r"(\.card \{\n  background: var\(--surface\);\n  border: 1px solid var\(--border\);\n  border-radius: var\(--radius\);\n)",
     r"\1  box-shadow: var(--shadow-1);\n",
     "cards stay flat"),
    ("删掉全局键盘焦点环", "css",
     r":focus-visible \{\n  outline: 2px solid var\(--accent\);\n  outline-offset: 2px;\n  border-radius: var\(--radius-xs\);\n\}\n",
     "",
     "css gives focus a visible ring"),
    ("把 outline 设成 none", "css",
     r"(\.skip-link:focus \{)",
     r"button:focus-visible { outline: none; }\n\1",
     "css never removes the focus outline"),
    ("删掉 prefers-reduced-motion 整块", "css",
     r"@media \(prefers-reduced-motion: reduce\) \{[\s\S]*?\n  \}\n\}\n", "",
     "css honours prefers-reduced-motion"),
    ("chip 边框退回饱和色", "css",
     r"\.pill-ok \{ background: var\(--ok-soft\); color: var\(--ok\); border-color: var\(--ok-line\); \}",
     ".pill-ok { background: var(--ok-soft); color: var(--ok); border-color: var(--ok); }",
     "status chips use the soft line tokens"),
    ("表头底色带撤掉", "css",
     r"(table\.data th \{\n)([\s\S]*?)(  background: var\(--surface-2\);\n)", r"\1\2",
     "table headers render as a tinted band"),
    ("删掉一个动效 token", "css", r"  --dur-2: 180ms;\n", "",
     "css defines the design-token layer"),
    ("深色主题漏掉顶栏底色", "css", r"    --topbar-bg: rgba\(16, 19, 23, 0\.84\);\n", "",
     "css keeps a full dark-theme token block"),
    ("引用一个拼错的变量", "css",
     r"(\.muted \{ color: var\(--text-muted\); \})",
     r"\1\n.muted-typo { color: var(--text-muted-typo); }",
     "every custom property referenced in css is defined there"),
    ("悬停改回 brightness 滤镜", "css",
     r"(button\.primary:hover:not\(:disabled\) \{ background: var\(--accent-hover\); border-color: var\(--accent-hover\); \})",
     r"\1\nbutton.primary:hover:not(:disabled) { filter: brightness(1.08); }",
     "css never lightens controls with a brightness filter"),
    ("弹窗撤掉最高层级投影", "css",
     r"(\.modal \{[\s\S]*?)  box-shadow: var\(--shadow-3\);\n", r"\1",
     "overlays carry the top elevation token"),
    ("CSS 侧把 sticky 偏移写死", "css", r"top: var\(--sticky-top\);", "top: 72px;",
     "css drives the sticky sidebar offset"),
    ("app.js 不再写 --topbar-h", "app", r"setProperty\('--topbar-h'", "setProperty('--topbar-height'",
     "app.js writes the measured header height"),
    # 对比度: 两张主题表必须**各自**求值 —— 只改浅色值却让深色也报红, 就说明
    # 深色表读到的是浅色的 token (写第一版时真的出现过这个缺陷)。
    ("浅色 accent-ink 退回原值", "css", r"  --accent-ink: #245ac7;", "  --accent-ink: #2c68df;",
     "contrast 浅色: 选中态文字 / 强调软底"),
    ("深色 accent-ink 调淡", "css", r"    --accent-ink: #7aa7ff;", "    --accent-ink: #4a6ea8;",
     "contrast 深色: 选中态文字 / 强调软底"),
    ("占位文字调淡", "css", r"  --text-soft: #667080;", "  --text-soft: #8b939f;",
     "contrast 浅色: 占位文字 / 卡片"),
    ("ok chip 文字调淡", "css", r"  --ok: #167a4a;", "  --ok: #2f9c66;",
     "contrast 浅色: ok chip"),
]


def run_audit() -> tuple[str, int]:
    """跑一次审计。encoding 显式给 utf-8 —— 中文断言名在 Windows 上会被按
    GBK 解码而抛 UnicodeDecodeError (项目里"GBK 子进程编码"那类坑的同族)。"""
    proc = subprocess.run(
        ["node", "scripts/ui_audit.js"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return (proc.stdout or "") + (proc.stderr or ""), proc.returncode


def failures_of(out: str) -> list[str]:
    return [line.strip()[2:] for line in out.splitlines() if line.strip().startswith("✗")]


def main() -> int:
    targets = {"css": CSS, "app": APP}
    originals = {key: path.read_text(encoding="utf-8") for key, path in targets.items()}

    out, code = run_audit()
    print("baseline:", out.strip().splitlines()[-1] if out.strip() else "?", "exit=", code)
    if code != 0:
        print("baseline is red — 先把基线修绿, 变异测试才有意义")
        return 1

    problems: list[str] = []
    for label, key, pattern, repl, expected in MUTATIONS:
        target = targets[key]
        mutated, count = re.subn(pattern, repl, originals[key])
        if count != 1:
            problems.append(f"{label}: 补丁没命中 (count={count})")
            print(f"[SKIP] {label}: 补丁没命中 (count={count})")
            continue
        target.write_text(mutated, encoding="utf-8", newline="")
        try:
            out, code = run_audit()
        finally:
            # 无论审计怎么退出都立刻还原: 一次 UnicodeDecodeError 就会把变异
            # 版本留在工作区, 下一次跑会拿"已经坏掉"的文件当基线。
            target.write_text(originals[key], encoding="utf-8", newline="")
        failures = failures_of(out)
        hit = [f for f in failures if expected in f]
        ok = code == 1 and bool(hit)
        if not ok:
            problems.append(f"{label}: 预期 {expected!r}, 实得 {failures}")
        first = failures[0][:72] if failures else "—"
        print(f"[{'OK  ' if ok else 'MISS'}] {label}: exit={code} red={len(failures)} | {first}")

    for key, path in targets.items():
        assert path.read_text(encoding="utf-8") == originals[key], f"{path} 没有被还原!"

    print()
    if problems:
        print("未命中/异常:")
        for item in problems:
            print("  -", item)
        return 1
    print(f"全部 {len(MUTATIONS)} 个变异都被精准捕获 (文件已还原)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
