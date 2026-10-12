# -*- coding: utf-8 -*-
import sys
sys.stdout.reconfigure(encoding="utf-8")
p = "src/application/bootstrap.py"
t = open(p, encoding="utf-8").read()

# 1) Runtime dataclass: add ai_timeout_seconds field
old1 = "    ai_mode: str = \"disabled\""
new1 = "    ai_mode: str = \"disabled\"\n    ai_timeout_seconds: int = 60"
assert old1 in t, "bootstrap: Runtime field not found"
t = t.replace(old1, new1, 1)

# 2) _build_ai_provider: return (provider, ai_mode, ai_config)
old2 = (
    "def _build_ai_provider(notes: list[str]) -> tuple[Any, str]:"
)
new2 = (
    "def _build_ai_provider(notes: list[str]) -> tuple[Any, str, Any]:"
)
assert old2 in t, "bootstrap: _build_ai_provider signature not found"
t = t.replace(old2, new2, 1)

old3 = (
    "    config = load_ai_config()\n"
    "    if not config.enabled:\n"
    "        return None, \"disabled\""
)
new3 = (
    "    config = load_ai_config()\n"
    "    if not config.enabled:\n"
    "        return None, \"disabled\", config"
)
assert old3 in t, "bootstrap: disabled return not found"
t = t.replace(old3, new3, 1)

old4 = (
    "        return provider, \"real\""
)
new4 = (
    "        return provider, \"real\", config"
)
assert old4 in t, "bootstrap: real return not found"
t = t.replace(old4, new4, 1)

old5 = (
    "    return FakeAIProvider(), \"fake\""
)
new5 = (
    "    return FakeAIProvider(), \"fake\", config"
)
assert old5 in t, "bootstrap: fake return not found"
t = t.replace(old5, new5, 1)

# 3) build_runtime: capture ai_config and pass into Runtime
old6 = (
    "    ai_provider, ai_mode = _build_ai_provider(notes)"
)
new6 = (
    "    ai_provider, ai_mode, ai_config = _build_ai_provider(notes)"
)
assert old6 in t, "bootstrap: build_runtime call not found"
t = t.replace(old6, new6, 1)

old7 = (
    "        ai_mode=ai_mode,\n"
    "        notes=tuple(notes),"
)
new7 = (
    "        ai_mode=ai_mode,\n"
    "        ai_timeout_seconds=ai_config.timeout_seconds,\n"
    "        notes=tuple(notes),"
)
assert old7 in t, "bootstrap: Runtime(...) ai_mode block not found"
t = t.replace(old7, new7, 1)

# 4) describe_runtime: expose ai_timeout_seconds (flat style, matching asr_mode)
old8 = (
    "        \"ai_mode\": runtime.ai_mode,\n"
    "        \"ai_enabled\": bool(runtime.workspace.ai_enabled),"
)
new8 = (
    "        \"ai_mode\": runtime.ai_mode,\n"
    "        \"ai_enabled\": bool(runtime.workspace.ai_enabled),\n"
    "        \"ai_timeout_seconds\": runtime.ai_timeout_seconds,"
)
assert old8 in t, "bootstrap: describe_runtime block not found"
t = t.replace(old8, new8, 1)

open(p, "w", encoding="utf-8").write(t)
print("bootstrap.py OK")

