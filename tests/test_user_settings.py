# -*- coding: utf-8 -*-
"""设置页后端: 用户设置存储 / 开机自启 / 更新检查 / /api/settings 端点。

设置页 (``#/settings``) 的三个开关落到这里:

- ``SettingsStore`` —— ``<data_dir>/settings.json`` 的读写 (原子、白名单、
  损坏回落);
- ``autostart`` —— Windows 注册表 Run 键 (测试全部走注入钩子, **绝不碰真实
  注册表**);
- ``update_check`` —— GitHub releases 比较 (fetch 注入, 网络失败不抛);
- 端点契约: GET / PATCH /api/settings, POST /api/settings/update-check。

与"能力真的生效"的验收口径一致: PATCH 之后既断言**响应**, 也断言
workspace 的内存状态 (``ai_enabled``) 与**落盘后新 Workspace 读到的值** ——
只证明端点活着是不够的 (跨层接线缺陷是本项目头号 bug 类型)。
"""

from __future__ import annotations

import http.client
import json
import os
import urllib.request

import pytest

from src.application.autostart import (
    default_launch_command,
    is_supported,
    read_run_command,
    set_autostart,
    write_run_command,
)
from src.application.settings import SETTINGS_FILENAME, SettingsStore, UserSettings
from src.application.update_check import check_for_update, parse_version
from src.application.workspace import Workspace


# ======================================================================
# SettingsStore / UserSettings
# ======================================================================


class TestSettingsStore:
    def test_missing_file_yields_defaults_without_notes(self, tmp_path):
        store = SettingsStore(str(tmp_path))
        settings, notes = store.load()
        assert settings == UserSettings()
        assert notes == []
        assert not os.path.exists(store.path)

    def test_update_roundtrips_through_the_file(self, tmp_path):
        store = SettingsStore(str(tmp_path))
        store.update(local_ai_enabled=True, autostart=False, auto_update_check=False)
        # 同目录新 store (模拟重启) 必须读到同样的值。
        reloaded, notes = SettingsStore(str(tmp_path)).load()
        assert reloaded.local_ai_enabled is True
        assert reloaded.autostart is False
        assert reloaded.auto_update_check is False
        assert notes == []

    def test_none_local_ai_means_never_set_not_false(self, tmp_path):
        """``local_ai_enabled=None`` 是有意义的状态 (跟随环境), 必须原样往返。"""
        store = SettingsStore(str(tmp_path))
        store.update(local_ai_enabled=None)
        reloaded, _ = store.load()
        assert reloaded.local_ai_enabled is None
        assert reloaded.to_dict()["local_ai_enabled"] is None

    def test_invalid_value_rejects_without_writing(self, tmp_path):
        store = SettingsStore(str(tmp_path))
        store.update(autostart=True)
        before = open(store.path, "rb").read()
        with pytest.raises(ValueError):
            store.update(local_ai_enabled="maybe")
        # 非法值绝不能落盘: 半合法的设置写下去比拒绝更糟。
        assert open(store.path, "rb").read() == before
        assert store.load()[0].autostart is True

    def test_unknown_setting_key_is_rejected(self, tmp_path):
        store = SettingsStore(str(tmp_path))
        with pytest.raises(ValueError):
            store.update(telepathy=True)

    def test_unknown_fields_in_the_file_are_ignored(self, tmp_path):
        """前向兼容: 新版本写的键, 旧版本读到时不该炸。"""
        store = SettingsStore(str(tmp_path))
        store.update(autostart=True)
        payload = json.loads(open(store.path, encoding="utf-8").read())
        payload["future_setting"] = {"nested": 1}
        with open(store.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        reloaded, notes = store.load()
        assert reloaded.autostart is True
        assert notes == []

    def test_corrupt_file_falls_back_to_defaults_with_a_note(self, tmp_path):
        store = SettingsStore(str(tmp_path))
        with open(store.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        settings, notes = store.load()
        assert settings == UserSettings()
        assert len(notes) == 1 and "settings file unreadable" in notes[0]

    def test_saved_file_is_normalized_json(self, tmp_path):
        store = SettingsStore(str(tmp_path))
        store.update(local_ai_enabled=False)
        text = open(store.path, encoding="utf-8").read()
        # 规范化 (sort_keys + 固定缩进): 同内容重写不产生字节差异。
        expected = json.dumps(
            {"autostart": False, "auto_update_check": True, "local_ai_enabled": False},
            ensure_ascii=False, sort_keys=True, indent=2,
        )
        assert text == expected + "\n"

    def test_settings_file_lives_at_the_data_dir_root(self, tmp_path):
        store = SettingsStore(str(tmp_path))
        store.update(autostart=False)
        assert os.path.isfile(os.path.join(str(tmp_path), SETTINGS_FILENAME))
        assert not os.path.isdir(os.path.join(str(tmp_path), SETTINGS_FILENAME))


class TestUserSettingsFromDict:
    def test_string_booleans_are_coerced(self):
        settings = UserSettings.from_dict(
            {"local_ai_enabled": "true", "autostart": "false", "auto_update_check": "1"}
        )
        assert settings.local_ai_enabled is True
        assert settings.autostart is False
        assert settings.auto_update_check is True

    def test_null_is_rejected_for_explicit_booleans(self):
        with pytest.raises(ValueError):
            UserSettings.from_dict({"autostart": None})

    def test_non_boolean_is_rejected(self):
        with pytest.raises(ValueError):
            UserSettings.from_dict({"auto_update_check": 3.5})


# ======================================================================
# autostart (注册表; 全部走注入钩子)
# ======================================================================


def _fake_registry():
    """内存字典当注册表。测试**绝不**碰真实 HKCU。"""
    values: dict[str, str] = {}

    def reader():
        return values.get("Loomind")

    def writer(command):
        values["Loomind"] = command

    def deleter():
        values.pop("Loomind", None)

    return values, reader, writer, deleter


class TestAutostart:
    def test_enable_disable_roundtrip_reads_back(self):
        values, reader, writer, deleter = _fake_registry()
        assert set_autostart(True, '"x" --y', reader=reader, writer=writer) is True
        assert values["Loomind"] == '"x" --y'
        assert set_autostart(False, '"x" --y', reader=reader, deleter=deleter) is False
        assert values == {}

    def test_delete_is_idempotent(self):
        values, reader, writer, deleter = _fake_registry()
        assert set_autostart(False, '"x"', reader=reader, deleter=deleter) is False
        assert set_autostart(False, '"x"', reader=reader, deleter=deleter) is False

    def test_empty_command_is_rejected(self):
        with pytest.raises(ValueError):
            write_run_command("   ")

    def test_launch_command_uses_the_module_launcher_in_dev(self):
        command = default_launch_command("D:/data")
        assert "-m src.application.launcher start" in command
        assert '--data-dir "D:/data"' in command

    def test_launch_command_is_the_exe_itself_when_frozen(self, monkeypatch):
        monkeypatch.setattr(
            "src.application.autostart.sys.executable", "C:/Apps/Loomind/Loomind.exe"
        )
        command = default_launch_command("D:/data")
        assert command.startswith('"C:/Apps/Loomind/Loomind.exe"')
        assert "--data-dir" in command
        assert "-m src.application.launcher" not in command

    def test_read_without_hooks_on_unsupported_platform_is_none(self, monkeypatch):
        monkeypatch.setattr("src.application.autostart.is_supported", lambda: False)
        assert read_run_command() is None

    def test_is_supported_is_a_plain_boolean(self):
        assert isinstance(is_supported(), bool)


# ======================================================================
# update_check (GitHub releases; fetch 注入)
# ======================================================================


def _release(tag="v1.2.0", body="notes"):
    return json.dumps(
        {
            "tag_name": tag,
            "html_url": "https://github.com/x/y/releases/tag/" + tag,
            "published_at": "2026-10-01T00:00:00Z",
            "body": body,
        }
    ).encode("utf-8")


class TestUpdateCheck:
    def test_update_available(self):
        result = check_for_update("1.2.0", fetch=lambda url, timeout: _release("v9.9.9"))
        assert result["ok"] is True
        assert result["latest"] == "9.9.9"
        assert result["update_available"] is True
        assert result["release_url"].endswith("v9.9.9")

    def test_up_to_date(self):
        result = check_for_update("9.9.9", fetch=lambda url, timeout: _release("v9.9.9"))
        assert result["ok"] is True
        assert result["update_available"] is False

    def test_network_failure_never_raises(self):
        def boom(url, timeout):
            raise TimeoutError("slow")

        result = check_for_update("1.2.0", fetch=boom)
        assert result["ok"] is False
        assert "slow" in result["error"]

    def test_missing_tag_is_reported(self):
        payload = json.dumps({"html_url": "https://x"}).encode("utf-8")
        result = check_for_update("1.2.0", fetch=lambda url, timeout: payload)
        assert result["ok"] is False
        assert "tag_name" in result["error"]

    def test_version_parsing(self):
        assert parse_version("v1.10.0") > parse_version("v1.9.0")
        assert parse_version("1.2.0-beta") == (1, 2, 0)
        assert parse_version("garbage") == ()


# ======================================================================
# Workspace 接线 (设置 -> 生效状态 -> 落盘 -> 重启读回)
# ======================================================================


class TestWorkspaceSettings:
    def test_snapshot_reports_effective_ai_state(self, tmp_path):
        workspace = Workspace(str(tmp_path / "data"), asr_mode="mock", ocr_mode="mock")
        snapshot = workspace.settings_snapshot()
        assert snapshot["ai"]["enabled"] is False
        assert snapshot["ai"]["mode"] == "disabled"
        assert snapshot["version"]
        assert snapshot["autostart"]["supported"] == is_supported()

    def test_toggling_local_ai_changes_the_live_switch(self, tmp_path):
        workspace = Workspace(str(tmp_path / "data"), asr_mode="mock", ocr_mode="mock")
        snapshot = workspace.update_settings(local_ai_enabled=True)
        # 生效状态 (内存真相), 不只是文件里的愿望值。
        assert snapshot["ai"]["enabled"] is True
        assert workspace.ai_enabled is True

    def test_persisted_setting_survives_a_restart(self, tmp_path):
        data = str(tmp_path / "data")
        Workspace(data, asr_mode="mock", ocr_mode="mock").update_settings(
            local_ai_enabled=False, auto_update_check=False
        )
        restarted = Workspace(data, asr_mode="mock", ocr_mode="mock")
        assert restarted.settings.local_ai_enabled is False
        assert restarted.settings.auto_update_check is False
        # 重启后生效状态也跟着设置走 (与 bootstrap 的同一条覆盖规则)。
        assert restarted.settings_snapshot()["ai"]["enabled"] is False

    def test_invalid_patch_changes_nothing(self, tmp_path):
        workspace = Workspace(str(tmp_path / "data"), asr_mode="mock", ocr_mode="mock")
        workspace.update_settings(local_ai_enabled=True)
        with pytest.raises(ValueError):
            workspace.update_settings(autostart="sometimes")
        assert workspace.ai_enabled is True
        assert workspace.settings.autostart is False

    def test_autostart_side_effects_are_rolled_back_on_registry_failure(
        self, tmp_path, monkeypatch
    ):
        import src.application.autostart as autostart_mod

        monkeypatch.setattr(autostart_mod, "is_supported", lambda: True)

        def failing_writer(command, **kwargs):
            raise OSError("registry locked")

        monkeypatch.setattr(autostart_mod, "write_run_command", failing_writer)
        workspace = Workspace(str(tmp_path / "data"), asr_mode="mock", ocr_mode="mock")
        with pytest.raises(OSError):
            workspace.update_settings(autostart=True)
        # 注册表写失败 -> 设置文件回滚, 文件与系统事实保持一致。
        assert workspace.settings.autostart is False
        reloaded, _ = SettingsStore(str(tmp_path / "data")).load()
        assert reloaded.autostart is False


# ======================================================================
# 端点契约 (live server)
# ======================================================================


@pytest.fixture
def workspace(tmp_path):
    return Workspace(str(tmp_path / "data"), asr_mode="mock", ocr_mode="mock")


@pytest.fixture
def fake_registry(monkeypatch):
    """端点测试用的假注册表 —— 绝不让 PATCH autostart 碰到真实 HKCU。"""
    import src.application.autostart as autostart_mod

    values: dict[str, str] = {}

    monkeypatch.setattr(autostart_mod, "is_supported", lambda: True)
    monkeypatch.setattr(autostart_mod, "read_run_command", lambda: values.get("Loomind"))

    def fake_set(enabled, command, **kwargs):
        if enabled:
            values["Loomind"] = command
        else:
            values.pop("Loomind", None)
        return values.get("Loomind") is not None

    monkeypatch.setattr(autostart_mod, "set_autostart", fake_set)
    monkeypatch.setattr(
        autostart_mod, "default_launch_command", lambda data_dir: "fake-launch-command"
    )
    return values


@pytest.fixture
def client(workspace):
    from src.api.server import create_server

    server = create_server(workspace, port=0)
    server.start()
    try:
        yield _Client(server.url)
    finally:
        server.stop()


class _Client:
    """最小 HTTP 客户端 (同 test_web_ui 的口径, 只取需要的部分)。"""

    def __init__(self, base_url):
        self.base = base_url
        self._connection = None

    def _conn(self):
        import urllib.parse as parse

        if self._connection is None:
            parsed = parse.urlsplit(self.base)
            self._connection = http.client.HTTPConnection(
                parsed.hostname, parsed.port, timeout=30
            )
        return self._connection

    def request(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        connection = self._conn()
        connection.request(method, path, body=data, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        parsed = json.loads(payload.decode("utf-8"))
        return response.status, parsed

    def get(self, path):
        return self.request("GET", path)

    def patch(self, path, body):
        return self.request("PATCH", path, body=body)

    def post(self, path, body=None):
        return self.request("POST", path, body=body)


class TestSettingsEndpoints:
    def test_get_returns_the_full_snapshot(self, client):
        status, payload = client.get("/api/settings")
        assert status == 200
        assert payload["success"] is True
        data = payload["data"]
        assert set(data) >= {"settings", "ai", "autostart", "auto_update", "version"}
        assert data["settings"] == {
            "local_ai_enabled": None, "autostart": False, "auto_update_check": True,
        }

    def test_patch_local_ai_takes_effect_and_persists(self, client, workspace, tmp_path):
        status, payload = client.patch("/api/settings", {"local_ai_enabled": True})
        assert status == 200
        assert payload["data"]["ai"]["enabled"] is True
        assert workspace.ai_enabled is True
        # 落盘: 同目录新 Workspace 读到同一个值。
        restarted = Workspace(str(tmp_path / "data"), asr_mode="mock", ocr_mode="mock")
        assert restarted.settings.local_ai_enabled is True

    def test_patch_autostart_writes_the_registry(self, client, fake_registry):
        status, payload = client.patch("/api/settings", {"autostart": True})
        assert status == 200
        assert payload["data"]["autostart"]["enabled"] is True
        assert fake_registry["Loomind"] == "fake-launch-command"
        # 读回来的是**注册表事实**, 不是设置文件里的愿望。
        status, payload = client.get("/api/settings")
        assert payload["data"]["autostart"]["enabled"] is True

    def test_patch_autostart_off_removes_the_registry_value(self, client, fake_registry):
        client.patch("/api/settings", {"autostart": True})
        status, payload = client.patch("/api/settings", {"autostart": False})
        assert status == 200
        assert payload["data"]["autostart"]["enabled"] is False
        assert fake_registry == {}

    def test_patch_rejects_unknown_field(self, client):
        status, payload = client.patch("/api/settings", {"telepathy": True})
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_patch_rejects_empty_body(self, client):
        status, payload = client.patch("/api/settings", {})
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_patch_rejects_invalid_value(self, client):
        status, payload = client.patch("/api/settings", {"autostart": "sometimes"})
        assert status == 400
        assert payload["error"]["code"] == "INVALID_INPUT"

    def test_update_check_endpoint(self, client, monkeypatch):
        import src.application.update_check as update_mod

        monkeypatch.setattr(
            update_mod,
            "check_for_update",
            lambda current: {
                "ok": True, "current": current, "latest": "9.9.9",
                "update_available": True, "release_url": "https://x/y",
                "published_at": "2026-10-01T00:00:00Z", "notes": "",
            },
        )
        status, payload = client.post("/api/settings/update-check")
        assert status == 200
        data = payload["data"]
        assert data["ok"] is True
        assert data["update_available"] is True
        assert data["current"]  # 当前版本真的被传进检查


# ======================================================================
# bootstrap 启动时的设置覆盖 (settings.json > 环境默认)
# ======================================================================


class _RuntimeConfig:
    pass


def _bootstrap_config(tmp_path):
    from src.application.config import AppConfig

    return AppConfig.load(
        env={},
        cli_overrides={
            "data_dir": str(tmp_path / "data"),
            "asr_mode": "mock",
            "ocr_config": {"kind": "mock"},
        },
    )


def _write_settings(data_dir, **fields):
    from src.application.settings import SettingsStore

    store = SettingsStore(str(data_dir))
    store.update(**fields)


def test_settings_enable_ai_without_env_credentials_uses_fake_provider(tmp_path):
    """设置里显式开启 + 环境没有凭证 -> 确定性 Fake + 诚实 note (与
    _build_ai_provider "无凭证回落 fake" 同口径, 绝不静默升级成 real)。"""
    import logging

    from src.application.bootstrap import build_runtime, close_runtime

    config = _bootstrap_config(tmp_path)
    config_data_dir = config.data_dir
    # 先建布局, 再写设置 (Workspace 构造时才读)。
    from src.application.data_dirs import ensure_data_layout

    ensure_data_layout(config_data_dir)
    _write_settings(config_data_dir, local_ai_enabled=True)

    built = build_runtime(config, logger=logging.getLogger("classroom.test"))
    try:
        assert built.workspace.ai_enabled is True
        assert built.workspace.settings_snapshot()["ai"]["mode"] == "fake"
        assert any("settings.json enables local AI" in note for note in built.notes)
    finally:
        close_runtime(built)


def test_settings_disable_overrides_env_enabled(tmp_path, monkeypatch):
    """环境开了但设置显式关 -> 以设置为准 (用户显式选择 > 环境默认)。"""
    import logging

    from src.application.bootstrap import build_runtime, close_runtime
    from src.application.data_dirs import ensure_data_layout

    monkeypatch.setenv("LOOMIND_AI_ENABLED", "true")
    monkeypatch.setenv("LOOMIND_AI_MODE", "fake")
    config = _bootstrap_config(tmp_path)
    ensure_data_layout(config.data_dir)
    _write_settings(config.data_dir, local_ai_enabled=False)

    built = build_runtime(config, logger=logging.getLogger("classroom.test"))
    try:
        assert built.workspace.ai_enabled is False
        assert any("overrides LOOMIND_AI_ENABLED" in note for note in built.notes)
    finally:
        close_runtime(built)
