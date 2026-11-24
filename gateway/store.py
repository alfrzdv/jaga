"""SQLite: schema, migrasi, CRUD, transisi status. Sumber kebenaran tunggal (§4).

Satu koneksi untuk seluruh proses (satu writer). Semua akses dari event loop thread.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

MIN_SQLITE = (3, 51, 3)


class Status(StrEnum):
    BARU = "baru"
    DIPROSES = "diproses"
    NUNGGU_APPROVE = "nunggu_approve"
    DISETUJUI = "disetujui"
    DIEKSEKUSI = "dieksekusi"
    SELESAI = "selesai"
    PERLU_CEK = "perlu_cek"
    GAGAL = "gagal"
    DITOLAK = "ditolak"


# Transisi yang sah (§4.4). Tidak ada jalur otomatis ke DISETUJUI.
TRANSITIONS: dict[Status, set[Status]] = {
    Status.BARU: {Status.DIPROSES},
    Status.DIPROSES: {Status.NUNGGU_APPROVE, Status.SELESAI, Status.GAGAL, Status.BARU},
    Status.NUNGGU_APPROVE: {Status.DISETUJUI, Status.DITOLAK, Status.NUNGGU_APPROVE},
    Status.DISETUJUI: {Status.DIEKSEKUSI, Status.PERLU_CEK},
    Status.DIEKSEKUSI: {Status.SELESAI, Status.PERLU_CEK},
    Status.PERLU_CEK: {Status.SELESAI, Status.DISETUJUI},
}


class InvalidTransition(Exception):
    pass


class StaleVersion(Exception):
    pass


@dataclass
class Job:
    id: int
    source: str
    external_id: str
    kind: str
    payload: dict | None
    status: Status
    proposal: dict | None
    version: int
    priority: str
    result: str | None
    attempts: int
    created_at: str
    updated_at: str


SCHEMA_V1 = """
-- TODO: tabel jobs (UNIQUE(source, external_id)), cursors, chat_messages,
--       actions_log, alerts, usage_log persis seperti §4.1-4.2
--       + index jobs(status), jobs(source, created_at)
"""


class Store:
    def __init__(self, path: Path | str):
        """path ':memory:' untuk tes."""
        # TODO: connect, row_factory, PRAGMA journal_mode=WAL, busy_timeout=5000,
        #       foreign_keys=ON; panggil migrate()
        raise NotImplementedError

    def sqlite_version_ok(self) -> bool:
        """False kalau < 3.51.3; main.py yang log warning."""
        raise NotImplementedError

    def migrate(self) -> None:
        """TODO: PRAGMA user_version sebagai nomor migrasi; v1 = SCHEMA_V1."""
        raise NotImplementedError

    # --- jobs -------------------------------------------------------------
    def create_job(
        self, source: str, external_id: str, kind: str, payload: dict, priority: str = "normal"
    ) -> tuple[int, bool]:
        """Return (job_id, created). created=False kalau (source, external_id) sudah ada."""
        # TODO: INSERT ... ON CONFLICT DO NOTHING, lalu SELECT id
        raise NotImplementedError

    def get_job(self, job_id: int) -> Job | None:
        raise NotImplementedError

    def claim_next(self) -> Job | None:
        """Ambil job BARU tertua (urgent dulu), pindahkan ke DIPROSES, attempts+1."""
        raise NotImplementedError

    def transition(
        self, job_id: int, to: Status, *, expected_version: int | None = None, **fields
    ) -> Job:
        """Ubah status dengan cek TRANSITIONS dan (opsional) version.

        fields boleh: proposal, result. Kalau proposal berubah, version+1.
        TODO: UPDATE ... WHERE id=? AND status=? [AND version=?]; rowcount 0 →
              InvalidTransition / StaleVersion (bedakan dengan SELECT ulang)
        """
        raise NotImplementedError

    def recover_on_start(self, stale_minutes: int) -> dict[str, int]:
        """§4.4: DIPROSES lama → BARU; DISETUJUI/DIEKSEKUSI → PERLU_CEK. Return hitungan."""
        raise NotImplementedError

    def list_jobs(self, statuses: list[Status] | None = None, limit: int = 20) -> list[Job]:
        raise NotImplementedError

    def recent_by_source(self, source_prefix: str, limit: int = 20) -> list[Job]:
        """Untuk context.py: item terbaru dari source tertentu (mis. 'web:lowongan')."""
        raise NotImplementedError

    # --- cursors ----------------------------------------------------------
    def get_cursor(self, source: str) -> dict | None:
        raise NotImplementedError

    def put_cursor(self, source: str, last_checked: str, meta: dict | None = None) -> None:
        raise NotImplementedError

    # --- chat -------------------------------------------------------------
    def add_chat(self, role: str, text: str, job_id: int | None = None) -> None:
        raise NotImplementedError

    def recent_chat(self, n: int) -> list[dict]:
        raise NotImplementedError

    # --- alerts -----------------------------------------------------------
    def should_send_alert(self, key: str, min_interval_s: int) -> bool:
        """Dedupe: True kalau belum pernah atau terakhir > min_interval_s lalu. Update count."""
        raise NotImplementedError

    # --- usage ------------------------------------------------------------
    def log_usage(
        self,
        job_id: int | None,
        model: str,
        num_turns: int | None,
        duration_ms: int | None,
        total_cost_usd: float | None,
    ) -> None:
        raise NotImplementedError

    def usage_today(self) -> dict[str, dict]:
        """Per kind: jumlah panggilan, total durasi, total cost. Untuk /status dan batas harian."""
        raise NotImplementedError

    # --- retensi ----------------------------------------------------------
    def purge_old(self, days: int) -> dict[str, int]:
        """§7: payload=NULL untuk job lebih tua dari `days`; hapus chat_messages lama.
        actions_log tidak disentuh."""
        raise NotImplementedError

    def status_summary(self) -> dict:
        """Untuk /status: jumlah per status, error terakhir, usage hari ini."""
        raise NotImplementedError
