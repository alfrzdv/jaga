"""Load .env dan validasi config. Gagal keras di awal kalau ada yang kurang.

Pesan error cuma menyebut nama key, tidak pernah nilainya (§8.8).
"""

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("TELEGRAM_BOT_TOKEN", "OWNER_CHAT_ID", "GATEWAY_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN")
DEFAULT_LIMITS = {"summarize": 40, "chat": 30, "digest": 12}
SYNC_FOLDERS = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "iclouddrive")
MIN_GATEWAY_TOKEN = 32


class ConfigError(Exception):
    pass


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

    @property
    def logs_dir(self) -> Path:
        return self.jaga_home / "logs"


def parse_env_file(path: Path) -> dict[str, str]:
    """Parser .env minimal: KEY=VALUE per baris, abaikan kosong dan '#'. File hilang = {}."""
    if not path.is_file():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def _in_sync_folder(path: Path) -> bool:
    return any(part.lower() in SYNC_FOLDERS for part in path.parts)


def load(env_path: Path | None = None, environ: dict[str, str] | None = None) -> Config:
    """Gabung .env + environ (environ menang), validasi, return Config."""
    file_vals = parse_env_file(env_path if env_path is not None else REPO_ROOT / ".env")
    env = file_vals | dict(os.environ if environ is None else environ)
    errors: list[str] = []

    def get(key: str, default: str = "") -> str:
        return env.get(key, "").strip() or default

    def as_int(key: str, default: str) -> int:
        try:
            return int(get(key, default))
        except ValueError:
            errors.append(f"{key} harus angka")
            return 0

    for key in REQUIRED:
        if not get(key):
            errors.append(f"{key} wajib diisi")

    owner = as_int("OWNER_CHAT_ID", "0") if get("OWNER_CHAT_ID") else 0
    if get("GATEWAY_TOKEN") and len(get("GATEWAY_TOKEN")) < MIN_GATEWAY_TOKEN:
        errors.append(f"GATEWAY_TOKEN minimal {MIN_GATEWAY_TOKEN} karakter")

    limits = dict(DEFAULT_LIMITS)
    for pair in filter(None, (p.strip() for p in get("DAILY_LIMITS").split(","))):
        kind, _, num = pair.partition("=")
        if not num.strip().isdigit():
            errors.append("DAILY_LIMITS formatnya kind=angka,kind=angka")
            break
        limits[kind.strip()] = int(num)

    quiet = (23, 6)
    start, sep, end = get("QUIET_HOURS", "23-6").partition("-")
    if not sep or not start.isdigit() or not end.isdigit():
        errors.append("QUIET_HOURS formatnya jam-jam, mis. 23-6")
    else:
        quiet = (int(start), int(end))
        if not all(0 <= h <= 23 for h in quiet):
            errors.append("QUIET_HOURS jam harus 0-23")

    home = Path(get("JAGA_HOME", str(Path.home() / ".jaga"))).expanduser()
    if _in_sync_folder(home):
        errors.append("JAGA_HOME tidak boleh di folder sync (OneDrive/Dropbox/Drive/iCloud)")

    cfg = Config(
        telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),
        owner_chat_id=owner,
        gateway_port=as_int("GATEWAY_PORT", "8787"),
        gateway_token=get("GATEWAY_TOKEN"),
        jaga_home=home,
        claude_bin=get("CLAUDE_BIN", "claude"),
        claude_oauth_token=get("CLAUDE_CODE_OAUTH_TOKEN"),
        worker_timeout_s=as_int("WORKER_TIMEOUT_S", "180"),
        daily_limits=limits,
        quiet_hours=quiet,
        digest_every_hours=as_int("DIGEST_EVERY_HOURS", "6"),
        morning_digest_hour=as_int("MORNING_DIGEST_HOUR", "7"),
        retention_days=as_int("RETENTION_DAYS", "30"),
        stale_minutes=as_int("STALE_MINUTES", "15"),
    )
    if errors:
        raise ConfigError("Config tidak valid:\n- " + "\n- ".join(errors))
    return cfg
