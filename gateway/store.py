"""SQLite: schema, migrasi, CRUD, transisi status. Sumber kebenaran tunggal (§4).

Satu koneksi untuk seluruh proses (satu writer). Semua akses dari event loop thread.
Waktu disimpan sebagai ISO UTC 'YYYY-MM-DDTHH:MM:SSZ' supaya bisa dibandingkan sebagai string.
"""

import json
import sqlite3
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

MIN_SQLITE = (3, 51, 3)
_UNSET = object()


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
# BARU → SELESAI cuma lewat finish_items() untuk kind=item.
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
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT,
    status TEXT NOT NULL DEFAULT 'baru',
    proposal TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('normal', 'urgent')),
    result TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source, external_id)
);
CREATE INDEX jobs_status ON jobs (status, kind);
CREATE INDEX jobs_source_created ON jobs (source, created_at);

CREATE TABLE cursors (
    source TEXT PRIMARY KEY,
    last_checked TEXT NOT NULL,
    meta TEXT
);

CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY,
    role TEXT NOT NULL,
    text TEXT NOT NULL,
    job_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE actions_log (
    id INTEGER PRIMARY KEY,
    job_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    params TEXT NOT NULL,
    approved_at TEXT,
    executed_at TEXT,
    outcome TEXT,
    detail TEXT
);

CREATE TABLE alerts (
    key TEXT PRIMARY KEY,
    last_sent_at TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE usage_log (
    id INTEGER PRIMARY KEY,
    job_id INTEGER,
    model TEXT NOT NULL,
    num_turns INTEGER,
    duration_ms INTEGER,
    total_cost_usd REAL,
    created_at TEXT NOT NULL
);
CREATE INDEX usage_created ON usage_log (created_at);
"""

MIGRATIONS = [SCHEMA_V1]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dump(obj) -> str | None:
    return None if obj is None else json.dumps(obj, ensure_ascii=False)


def _load(text: str | None):
    return None if text is None else json.loads(text)


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        source=row["source"],
        external_id=row["external_id"],
        kind=row["kind"],
        payload=_load(row["payload"]),
        status=Status(row["status"]),
        proposal=_load(row["proposal"]),
        version=row["version"],
        priority=row["priority"],
        result=row["result"],
        attempts=row["attempts"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class Store:
    def __init__(self, path: Path | str, clock: Callable[[], datetime] | None = None):
        """path ':memory:' untuk tes. clock harus return datetime aware."""
        self.clock = clock or (lambda: datetime.now(UTC))
        # isolation_level=None: autocommit per statement; blok multi-statement lewat transaction()
        self.conn = sqlite3.connect(str(path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """BEGIN IMMEDIATE ... COMMIT; ROLLBACK kalau error."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def _now(self) -> str:
        return _iso(self.clock())

    def sqlite_version_ok(self) -> bool:
        return sqlite3.sqlite_version_info >= MIN_SQLITE

    def migrate(self) -> None:
        current = self.conn.execute("PRAGMA user_version").fetchone()[0]
        for version, script in enumerate(MIGRATIONS[current:], start=current + 1):
            # executescript commit sendiri; user_version ikut di script yang sama
            self.conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version={version};\nCOMMIT;")

    # --- jobs -------------------------------------------------------------
    def create_job(
        self, source: str, external_id: str, kind: str, payload: dict, priority: str = "normal"
    ) -> tuple[int, bool]:
        """Return (job_id, created). created=False kalau (source, external_id) sudah ada."""
        now = self._now()
        cur = self.conn.execute(
            "INSERT INTO jobs (source, external_id, kind, payload, priority, created_at,"
            " updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (source, external_id) DO NOTHING",
            (source, external_id, kind, _dump(payload), priority, now, now),
        )
        if cur.rowcount:
            return cur.lastrowid, True
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE source = ? AND external_id = ?", (source, external_id)
        ).fetchone()
        return row["id"], False

    def get_job(self, job_id: int) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row else None

    def claim_next(self, kinds: Iterable[str]) -> Job | None:
        """Ambil job BARU tertua dari `kinds` (urgent dulu), pindah ke DIPROSES, attempts+1."""
        kinds = list(kinds)
        if not kinds:
            return None
        marks = ",".join("?" * len(kinds))
        row = self.conn.execute(
            f"UPDATE jobs SET status = 'diproses', attempts = attempts + 1, updated_at = ?"
            f" WHERE id = (SELECT id FROM jobs WHERE status = 'baru' AND kind IN ({marks})"
            f"  ORDER BY priority = 'urgent' DESC, id LIMIT 1)"
            f" RETURNING *",
            (self._now(), *kinds),
        ).fetchone()
        return _row_to_job(row) if row else None

    def transition(
        self,
        job_id: int,
        to: Status,
        *,
        expected_version: int | None = None,
        proposal=_UNSET,
        result=_UNSET,
    ) -> Job:
        """Ubah status dengan cek TRANSITIONS dan (opsional) version.

        Version naik kalau proposal diganti dan sebelumnya sudah ada proposal.
        Raise KeyError (job tidak ada), InvalidTransition, StaleVersion.
        """
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        if to not in TRANSITIONS.get(job.status, set()):
            raise InvalidTransition(f"job {job_id}: {job.status} → {to}")
        if expected_version is not None and job.version != expected_version:
            raise StaleVersion(f"job {job_id}: versi {expected_version}, sekarang {job.version}")

        sets = ["status = ?", "updated_at = ?"]
        args: list = [str(to), self._now()]
        if proposal is not _UNSET:
            sets.append("proposal = ?")
            args.append(_dump(proposal))
            if job.proposal is not None and proposal != job.proposal:
                sets.append("version = version + 1")
        if result is not _UNSET:
            sets.append("result = ?")
            args.append(result)
        # guard status + version di WHERE: kalau ada yang ubah di antara SELECT dan UPDATE,
        # rowcount 0 dan kita anggap basi
        cur = self.conn.execute(
            f"UPDATE jobs SET {', '.join(sets)} WHERE id = ? AND status = ? AND version = ?",
            (*args, job_id, str(job.status), job.version),
        )
        if cur.rowcount == 0:
            raise StaleVersion(f"job {job_id} berubah saat transisi")
        return self.get_job(job_id)

    def release(self, job_id: int, result: str) -> Job:
        """DIPROSES → BARU tanpa menghitung percobaan (masalahnya bukan di job, mis. auth)."""
        cur = self.conn.execute(
            "UPDATE jobs SET status = 'baru', attempts = MAX(attempts - 1, 0), result = ?,"
            " updated_at = ? WHERE id = ? AND status = 'diproses'",
            (result, self._now(), job_id),
        )
        if cur.rowcount == 0:
            raise InvalidTransition(f"job {job_id} tidak sedang diproses")
        return self.get_job(job_id)

    def recover_on_start(self, stale_minutes: int) -> dict[str, int]:
        """§4.4: DIPROSES lama → BARU; DISETUJUI/DIEKSEKUSI → PERLU_CEK (tidak pernah di-reset)."""
        now = self.clock()
        cutoff = _iso(now - timedelta(minutes=stale_minutes))
        with self.transaction():
            reset = self.conn.execute(
                "UPDATE jobs SET status = 'baru', updated_at = ?"
                " WHERE status = 'diproses' AND updated_at < ?",
                (_iso(now), cutoff),
            ).rowcount
            perlu_cek = self.conn.execute(
                "UPDATE jobs SET status = 'perlu_cek', updated_at = ?,"
                " result = COALESCE(result, 'nyangkut saat Gateway mati')"
                " WHERE status IN ('disetujui', 'dieksekusi')",
                (_iso(now),),
            ).rowcount
        return {"reset": reset, "perlu_cek": perlu_cek}

    def list_jobs(self, statuses: list[Status] | None = None, limit: int = 20) -> list[Job]:
        if statuses:
            marks = ",".join("?" * len(statuses))
            rows = self.conn.execute(
                f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY id DESC LIMIT ?",
                (*map(str, statuses), limit),
            )
        else:
            rows = self.conn.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
        return [_row_to_job(r) for r in rows]

    def recent_by_source(self, source_prefix: str, limit: int = 20) -> list[Job]:
        """Untuk context.py: item terbaru dari source dengan prefix tertentu."""
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE source = ? OR source LIKE ? ESCAPE '\\'"
            " ORDER BY created_at DESC, id DESC LIMIT ?",
            (source_prefix, _like_prefix(source_prefix), limit),
        )
        return [_row_to_job(r) for r in rows]

    # --- item & digest (§7) ----------------------------------------------
    def pending_items(self, limit: int = 200) -> list[Job]:
        rows = self.conn.execute(
            "SELECT * FROM jobs WHERE kind = 'item' AND status = 'baru' ORDER BY id LIMIT ?",
            (limit,),
        )
        return [_row_to_job(r) for r in rows]

    def count_waiting(self, kind: str) -> int:
        """Jumlah job BARU untuk satu kind (untuk notif batas harian)."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE kind = ? AND status = 'baru'", (kind,)
        ).fetchone()[0]

    def has_open_digest(self) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM jobs WHERE kind = 'digest' AND status IN ('baru', 'diproses') LIMIT 1"
        ).fetchone()
        return row is not None

    def finish_items(self, ids: Iterable[int], result: str) -> int:
        """Item yang masih BARU → SELESAI setelah digest-nya sukses. Return jumlah."""
        ids = list(ids)
        if not ids:
            return 0
        marks = ",".join("?" * len(ids))
        return self.conn.execute(
            f"UPDATE jobs SET status = 'selesai', result = ?, updated_at = ?"
            f" WHERE kind = 'item' AND status = 'baru' AND id IN ({marks})",
            (result, self._now(), *ids),
        ).rowcount

    # --- cursors ----------------------------------------------------------
    def get_cursor(self, source: str) -> dict | None:
        row = self.conn.execute(
            "SELECT last_checked, meta FROM cursors WHERE source = ?", (source,)
        ).fetchone()
        if row is None:
            return None
        return {"last_checked": row["last_checked"], "meta": _load(row["meta"])}

    def put_cursor(self, source: str, last_checked: str, meta: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO cursors (source, last_checked, meta) VALUES (?, ?, ?)"
            " ON CONFLICT (source) DO UPDATE SET last_checked = excluded.last_checked,"
            " meta = excluded.meta",
            (source, last_checked, _dump(meta)),
        )

    # --- chat -------------------------------------------------------------
    def add_chat(self, role: str, text: str, job_id: int | None = None) -> None:
        self.conn.execute(
            "INSERT INTO chat_messages (role, text, job_id, created_at) VALUES (?, ?, ?, ?)",
            (role, text, job_id, self._now()),
        )

    def recent_chat(self, n: int) -> list[dict]:
        """N pesan terakhir, urut lama → baru."""
        rows = self.conn.execute(
            "SELECT role, text, created_at FROM chat_messages ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    # --- alerts -----------------------------------------------------------
    def should_send_alert(self, key: str, min_interval_s: int) -> bool:
        """True kalau belum pernah atau terakhir > min_interval_s lalu. count selalu naik."""
        now = self.clock()
        with self.transaction():
            row = self.conn.execute(
                "SELECT last_sent_at FROM alerts WHERE key = ?", (key,)
            ).fetchone()
            send = row is None or row["last_sent_at"] < _iso(
                now - timedelta(seconds=min_interval_s)
            )
            self.conn.execute(
                "INSERT INTO alerts (key, last_sent_at, count) VALUES (?, ?, 1)"
                " ON CONFLICT (key) DO UPDATE SET count = count + 1,"
                " last_sent_at = CASE WHEN ? THEN excluded.last_sent_at ELSE last_sent_at END",
                (key, _iso(now), send),
            )
        return send

    # --- usage ------------------------------------------------------------
    def log_usage(
        self,
        job_id: int | None,
        model: str,
        num_turns: int | None,
        duration_ms: int | None,
        total_cost_usd: float | None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO usage_log (job_id, model, num_turns, duration_ms, total_cost_usd,"
            " created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, model, num_turns, duration_ms, total_cost_usd, self._now()),
        )

    def _today_start(self) -> str:
        """Awal hari ini menurut jam lokal laptop, dalam UTC."""
        local = self.clock().astimezone()
        return _iso(local.replace(hour=0, minute=0, second=0, microsecond=0))

    def usage_today(self) -> dict[str, dict]:
        """Per kind: jumlah panggilan, total durasi, total cost (list price)."""
        rows = self.conn.execute(
            "SELECT COALESCE(j.kind, '?') AS kind, COUNT(*) AS calls,"
            " COALESCE(SUM(u.duration_ms), 0) AS duration_ms,"
            " COALESCE(SUM(u.total_cost_usd), 0) AS cost_usd"
            " FROM usage_log u LEFT JOIN jobs j ON j.id = u.job_id"
            " WHERE u.created_at >= ? GROUP BY 1",
            (self._today_start(),),
        )
        return {r["kind"]: {k: r[k] for k in ("calls", "duration_ms", "cost_usd")} for r in rows}

    def calls_today(self, kind: str) -> int:
        return self.usage_today().get(kind, {}).get("calls", 0)

    # --- retensi ----------------------------------------------------------
    def purge_old(self, days: int) -> dict[str, int]:
        """§7: payload=NULL untuk job lebih tua dari `days`; hapus chat_messages lama.
        actions_log tidak disentuh."""
        cutoff = _iso(self.clock() - timedelta(days=days))
        with self.transaction():
            payload = self.conn.execute(
                "UPDATE jobs SET payload = NULL WHERE created_at < ? AND payload IS NOT NULL",
                (cutoff,),
            ).rowcount
            chat = self.conn.execute(
                "DELETE FROM chat_messages WHERE created_at < ?", (cutoff,)
            ).rowcount
        return {"payload": payload, "chat": chat}

    def status_summary(self) -> dict:
        """Untuk /status: jumlah per status, error terakhir, usage hari ini."""
        counts = {
            r["status"]: r["n"]
            for r in self.conn.execute("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
        }
        err = self.conn.execute(
            "SELECT id, kind, status, result, updated_at FROM jobs"
            " WHERE status IN ('gagal', 'perlu_cek') ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()
        last_error = None
        if err:
            last_error = {
                "job_id": err["id"],
                "kind": err["kind"],
                "status": err["status"],
                "result": err["result"],
                "at": err["updated_at"],
            }
        return {"counts": counts, "last_error": last_error, "usage_today": self.usage_today()}


def _like_prefix(prefix: str) -> str:
    """'web:' → 'web:%' dengan escape karakter LIKE. 'web:lowongan' juga cocok 'web:lowongan:x'."""
    esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return esc + ("%" if prefix.endswith(":") else ":%")
