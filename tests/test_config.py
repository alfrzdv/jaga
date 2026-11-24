from pathlib import Path

import pytest

from gateway.config import ConfigError, load, parse_env_file

SECRET = "rahasia-bot-123:ABCDEF"


def base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "TELEGRAM_BOT_TOKEN": SECRET,
        "OWNER_CHAT_ID": "12345",
        "GATEWAY_TOKEN": "x" * 40,
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-oauth-rahasia",
        "JAGA_HOME": str(tmp_path / "jaga"),
    }


def test_parse_env_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text(
        "# komentar\n\nA=1\nB = dua \nC=\"pakai kutip\"\nD='satu=dua'\nKOSONG=\n",
        encoding="utf-8",
    )
    assert parse_env_file(f) == {
        "A": "1",
        "B": "dua",
        "C": "pakai kutip",
        "D": "satu=dua",
        "KOSONG": "",
    }


def test_parse_env_file_missing_returns_empty(tmp_path):
    assert parse_env_file(tmp_path / "nggak-ada.env") == {}


def test_load_defaults(tmp_path):
    cfg = load(env_path=tmp_path / "none.env", environ=base_env(tmp_path))
    assert cfg.owner_chat_id == 12345
    assert cfg.gateway_port == 8787
    assert cfg.claude_bin == "claude"
    assert cfg.daily_limits == {"summarize": 40, "chat": 30, "digest": 12}
    assert cfg.quiet_hours == (23, 6)
    assert cfg.db_path == tmp_path / "jaga" / "jaga.db"
    assert cfg.claude_config_dir == tmp_path / "jaga" / "claude-config"


def test_environ_overrides_file(tmp_path):
    f = tmp_path / ".env"
    f.write_text("GATEWAY_PORT=9000\nOWNER_CHAT_ID=1\n", encoding="utf-8")
    env = base_env(tmp_path) | {"GATEWAY_PORT": "9100"}
    cfg = load(env_path=f, environ=env)
    assert cfg.gateway_port == 9100
    assert cfg.owner_chat_id == 12345


def test_missing_keys_named_without_values(tmp_path):
    env = base_env(tmp_path)
    del env["OWNER_CHAT_ID"]
    env["TELEGRAM_BOT_TOKEN"] = ""
    with pytest.raises(ConfigError) as e:
        load(env_path=tmp_path / "none.env", environ=env)
    msg = str(e.value)
    assert "OWNER_CHAT_ID" in msg
    assert "TELEGRAM_BOT_TOKEN" in msg
    assert "sk-oauth-rahasia" not in msg


def test_invalid_values(tmp_path):
    env = base_env(tmp_path) | {
        "OWNER_CHAT_ID": "bukan-angka",
        "GATEWAY_TOKEN": "pendek",
        "QUIET_HOURS": "23",
        "DAILY_LIMITS": "summarize=banyak",
    }
    with pytest.raises(ConfigError) as e:
        load(env_path=tmp_path / "none.env", environ=env)
    msg = str(e.value)
    for key in ("OWNER_CHAT_ID", "GATEWAY_TOKEN", "QUIET_HOURS", "DAILY_LIMITS"):
        assert key in msg
    assert "pendek" not in msg
    assert "bukan-angka" not in msg


def test_daily_limits_partial_override(tmp_path):
    env = base_env(tmp_path) | {"DAILY_LIMITS": "chat=5"}
    cfg = load(env_path=tmp_path / "none.env", environ=env)
    assert cfg.daily_limits == {"summarize": 40, "chat": 5, "digest": 12}


@pytest.mark.parametrize("folder", ["OneDrive", "Dropbox", "Google Drive", "iCloudDrive"])
def test_jaga_home_in_sync_folder_rejected(tmp_path, folder):
    env = base_env(tmp_path) | {"JAGA_HOME": str(tmp_path / folder / "jaga")}
    with pytest.raises(ConfigError) as e:
        load(env_path=tmp_path / "none.env", environ=env)
    assert "JAGA_HOME" in str(e.value)
