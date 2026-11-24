"""Wrapper `claude -p` + validasi output + antrean Worker (concurrency 1). Lihat §5, §6."""

from dataclasses import dataclass

from .config import Config
from .store import Store

# Per kind: model, tools (--tools), max_turns, max_budget_usd. §5.2-5.3.
KINDS: dict[str, dict] = {
    "summarize": {"model": "haiku", "tools": "", "max_turns": 3, "max_budget_usd": 0.10},
    "chat": {"model": "haiku", "tools": "", "max_turns": 3, "max_budget_usd": 0.10},
}

MAX_ATTEMPTS = 3  # 1 percobaan + 2 retry (§4.4)


@dataclass
class WorkerResult:
    ok: bool
    output: dict | None  # structured_output
    error: str | None  # alasan gagal singkat, tanpa isi data
    model: str
    num_turns: int | None
    duration_ms: int | None
    total_cost_usd: float | None
    permission_denials: list


def build_command(config: Config, kind: str, prompt: str) -> list[str]:
    """Susun argv sesuai template §5.2. Schema dibaca dari worker_dir/schemas/<kind>.json."""
    raise NotImplementedError


def build_env(config: Config, base: dict[str, str]) -> dict[str, str]:
    """Salin env, hapus ANTHROPIC_API_KEY, set CLAUDE_CODE_DISABLE_AUTO_MEMORY=1,
    CLAUDE_CONFIG_DIR, CLAUDE_CODE_OAUTH_TOKEN."""
    raise NotImplementedError


def parse_output(stdout: bytes, model: str) -> WorkerResult:
    """Validasi JSON CLI (§4.6, findings Fase 0).

    Gagal kalau: stdout kosong (timeout di Windows), bukan JSON, is_error true,
    subtype != success, atau structured_output tidak ada.
    permission_denials tidak bikin gagal, tapi dicatat.
    """
    raise NotImplementedError


async def run_claude(config: Config, kind: str, prompt: str) -> WorkerResult:
    """asyncio.create_subprocess_exec, cwd=worker_dir, stdin=DEVNULL, timeout config.

    TODO:
    - Windows: creationflags=CREATE_NEW_PROCESS_GROUP; saat timeout kirim CTRL_BREAK_EVENT,
      tunggu 10 detik, lalu kill(). POSIX: SIGINT lalu kill().
    - timeout → WorkerResult(ok=False, error='timeout', usage None)
    """
    raise NotImplementedError


def build_prompt(kind: str, job_payload: dict, context: str | None = None) -> str:
    """Bungkus data sumber di <data>...</data> (§5.6). Escape '</data>' di dalam data."""
    raise NotImplementedError


class WorkerLoop:
    def __init__(self, config: Config, store: Store, on_done):
        """on_done(job, result) dipanggil setelah job selesai/gagal → bot/notifier."""
        raise NotImplementedError

    def wake(self) -> None:
        """Dipanggil api.py / bot.py saat ada job baru."""
        raise NotImplementedError

    async def run(self) -> None:
        """Loop: claim_next → cek batas harian → run_claude → simpan.

        TODO:
        - batas harian per kind terlewati: job dikembalikan ke BARU, owner dinotif SEKALI
          per kind per hari (alert key 'limit:<kind>:<tanggal>')
        - sukses: log_usage, transition SELESAI dengan proposal=output (Fase 1 belum ada aksi;
          aksi_diajukan diabaikan + di-log kalau tidak kosong)
        - gagal: attempts < MAX_ATTEMPTS → BARU (dengan backoff), selainnya GAGAL + notif
        - concurrency 1: satu job sekali jalan
        """
        raise NotImplementedError
