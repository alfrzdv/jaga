"""Load .env dan validasi config. Gagal keras di awal kalau ada yang kurang."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    telegram_bot_token: str
    owner_chat_id: int
    gateway_port: int
    gateway_token: str
    jaga_home: Path
    claude_bin: str
    claude_oauth_token: str
    worker_timeout_s: int
    daily_limits: dict[str, int]
    quiet_hours: tuple[int, int]  # (mulai, selesai), jam lokal
    digest_every_hours: int
    morning_digest_hour: int
    retention_days: int
    stale_minutes: int

    @property
    def db_path(self) -> Path:
        return self.jaga_home / "jaga.db"

    @property
    def worker_dir(self) -> Path:
        return self.jaga_home / "worker"

    @property
    def claude_config_dir(self) -> Path:
        return self.jaga_home / "claude-config"


def parse_env_file(path: Path) -> dict[str, str]:
    """Parser .env minimal: KEY=VALUE per baris, abaikan kosong dan '#'."""
    # TODO: implementasi; tanpa interpolasi, tanpa multiline
    raise NotImplementedError


def load(env_path: Path | None = None, environ: dict[str, str] | None = None) -> Config:
    """Gabung .env + os.environ (os.environ menang), validasi, return Config.

    TODO:
    - wajib: TELEGRAM_BOT_TOKEN, OWNER_CHAT_ID (int), GATEWAY_TOKEN (>= 32 char),
      CLAUDE_CODE_OAUTH_TOKEN
    - JAGA_HOME default ~/.jaga; tolak kalau path ada di folder sync
      (OneDrive/Dropbox/Google Drive) karena §4.3
    - parse DAILY_LIMITS "summarize=40,chat=30" dan QUIET_HOURS "23-6"
    - error message sebut nama key, JANGAN print nilainya (aturan §8.8)
    """
    raise NotImplementedError
