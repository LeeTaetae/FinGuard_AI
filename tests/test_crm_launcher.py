import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotenv import dotenv_values

if sys.platform != "win32":
    pytest.skip("Windows launcher", allow_module_level=True)

import crm_launcher as launcher


def test_local_config_preserves_process_keys_and_loads_local_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher.os, "environ", {
        "FINGUARD_SECRET_KEY": "existing-session-key",
        "FINGUARD_ROBOT_TOKEN": "existing-robot-token",
    })
    (tmp_path / ".env").write_text("UIPATH_ACCESS_TOKEN=test-gateway\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text(
        "FINGUARD_SECRET_KEY=local-session-key\n"
        "FINGUARD_ORCHESTRATOR_CLIENT_SECRET=local-test-secret\n"
        "UIPATH_ACCESS_TOKEN=stale-local-override\n",
        encoding="utf-8",
    )
    launcher.load_local_environment()
    assert os.environ["FINGUARD_SECRET_KEY"] == "existing-session-key"
    assert os.environ["FINGUARD_ROBOT_TOKEN"] == "existing-robot-token"
    assert os.environ["FINGUARD_ORCHESTRATOR_CLIENT_SECRET"] == "local-test-secret"
    assert os.environ["UIPATH_ACCESS_TOKEN"] == "test-gateway"


@pytest.mark.parametrize(("expired", "check_only", "refresh_count"), [
    (False, False, 0), (True, True, 0), (True, False, 1),
])
def test_only_expired_token_during_start_triggers_auth(monkeypatch, expired, check_only, refresh_count):
    calls = []
    monkeypatch.setattr(launcher, "configuration_issues", lambda: [])
    monkeypatch.setattr(launcher, "gateway_expired", lambda: expired)
    monkeypatch.setattr(launcher, "refresh_gateway", lambda: calls.append(True) or True)
    assert launcher.check_settings(check_only) is not (expired and check_only)
    assert len(calls) == refresh_count


def test_gateway_refresh_keeps_other_local_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher.os, "environ", {
        "UIPATH_ACCESS_TOKEN": "previous-credential",
        "FINGUARD_SECRET_KEY": "existing-session-key",
        "FINGUARD_ROBOT_TOKEN": "existing-robot-token",
    })
    (tmp_path / ".env").write_text("FINGUARD_ORCHESTRATOR_SCOPE=OR.Default\n", encoding="utf-8")
    fresh = {key: f"test-{key}" for key in launcher.GATEWAY_KEYS}

    def authenticate(args, *, cwd, env, **kwargs):
        assert args[-2:] == ["auth", "--force"]
        assert "UIPATH_ACCESS_TOKEN" not in env
        assert Path(cwd) != tmp_path
        (Path(cwd) / ".env").write_text(
            "".join(f"{key}={value}\n" for key, value in fresh.items()), encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher.subprocess, "run", authenticate)
    assert launcher.refresh_gateway()
    assert dotenv_values(tmp_path / ".env") == {**fresh, "FINGUARD_ORCHESTRATOR_SCOPE": "OR.Default"}
    assert os.environ["FINGUARD_SECRET_KEY"] == "existing-session-key"
    assert os.environ["FINGUARD_ROBOT_TOKEN"] == "existing-robot-token"


def test_save_local_config_copies_only_supported_finguard_settings(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    monkeypatch.setattr(launcher.os, "environ", {
        "FINGUARD_SECRET_KEY": "sample-key-for-test",
        "UIPATH_ACCESS_TOKEN": "gateway-not-copied",
        "OPENAI_API_KEY": "provider-not-copied",
    })
    assert launcher.save_local_config() == 0
    assert dotenv_values(tmp_path / ".env.local") == {"FINGUARD_SECRET_KEY": "sample-key-for-test"}
    assert "sample-key-for-test" not in capsys.readouterr().out
