from dataclasses import replace
from pathlib import Path

import pytest

from gateway.config import DEFAULT_LIMITS, Config

FAKE_CLAUDE = Path(__file__).parent / "fake_claude.py"


@pytest.fixture
def make_config(tmp_path):
    def make(**overrides) -> Config:
        cfg = Config(
            telegram_bot_token="123:fake",
            owner_chat_id=111,
            gateway_port=8787,
            gateway_token="t" * 40,
            jaga_home=tmp_path / "jaga",
            claude_bin=str(FAKE_CLAUDE),
            claude_oauth_token="oauth-fake",
            worker_timeout_s=20,
            daily_limits=dict(DEFAULT_LIMITS),
            quiet_hours=(23, 6),
            digest_every_hours=6,
            morning_digest_hour=7,
            retention_days=30,
            stale_minutes=15,
        )
        return replace(cfg, **overrides)

    return make
