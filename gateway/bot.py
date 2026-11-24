"""Handler Telegram: owner lock, perintah, chat, tombol (§4.7, §8.4).

Semua balasan lewat Notifier (rate limit + jam tenang). Pengecualian: answerCallbackQuery,
yang harus langsung supaya HP tidak loading terus.
"""

import logging
import re
from collections.abc import Callable

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from .config import Config
from .notifier import TELEGRAM_MAX, Outgoing
from .store import Job, Store
from .worker import WorkerResult

log = logging.getLogger(__name__)

CALLBACK_RE = re.compile(r"j:(\d+):(\d+):([vaet])")
STALE_TEXT = "Draft udah berubah, pake pesan terbaru"


def parse_callback(data: str) -> tuple[int, int, str] | None:
    """'j:<job_id>:<version>:<aksi>' → (job_id, version, aksi). None kalau format salah."""
    m = CALLBACK_RE.fullmatch(data or "")
    if not m:
        return None
    return int(m[1]), int(m[2]), m[3]


def _chunks(text: str, size: int = TELEGRAM_MAX) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


class Handlers:
    def __init__(self, owner_chat_id: int, store: Store, notifier, wake: Callable[[], None]):
        self.owner = owner_chat_id
        self.store = store
        self.notifier = notifier
        self.wake = wake

    async def owner_lock(self, update: Update, context) -> None:
        """Group -1: hentikan SEMUA update yang bukan dari owner, termasuk callback (§8.4)."""
        chat = getattr(update, "effective_chat", None)
        user = getattr(update, "effective_user", None)
        if chat is None or chat.id != self.owner or (user is not None and user.id != self.owner):
            log.info("update dari non-owner diabaikan")
            raise ApplicationHandlerStop

    async def on_text(self, update: Update, context) -> None:
        msg = update.effective_message
        self.store.add_chat("user", msg.text)
        _, created = self.store.create_job(
            "chat", str(msg.message_id), "chat", {"text": msg.text, "message_id": msg.message_id}
        )
        if created:
            self.wake()

    async def cmd_status(self, update: Update, context) -> None:
        s = self.store.status_summary()
        counts = ", ".join(f"{k}: {v}" for k, v in sorted(s["counts"].items())) or "kosong"
        lines = ["Status Jaga", f"Job: {counts}"]
        if err := s["last_error"]:
            lines.append(
                f"Error terakhir: #{err['job_id']} {err['kind']} ({err['status']}) "
                f"{err['result'] or '-'}, {err['at']}"
            )
        else:
            lines.append("Error terakhir: -")
        usage = s["usage_today"]
        if usage:
            parts = [
                f"{kind} {u['calls']}x, {u['duration_ms'] / 1000:.0f} dtk, ${u['cost_usd']:.3f}"
                for kind, u in sorted(usage.items())
            ]
            lines.append("Worker hari ini: " + "; ".join(parts) + " (list price)")
        else:
            lines.append("Worker hari ini: belum dipakai")
        await self.notifier.send(Outgoing("\n".join(lines)))

    async def cmd_jobs(self, update: Update, context) -> None:
        jobs = self.store.list_jobs(limit=10)
        if not jobs:
            await self.notifier.send(Outgoing("Belum ada job."))
            return
        lines = [f"#{j.id} {j.source} {j.kind} {j.status} {j.updated_at}" for j in jobs]
        await self.notifier.send(Outgoing("\n".join(lines)))

    async def on_callback(self, update: Update, context) -> None:
        query = update.callback_query
        parsed = parse_callback(query.data)
        job = self.store.get_job(parsed[0]) if parsed else None
        if job is None:
            await query.answer("Tombol ini udah ga berlaku")
            return
        _, version, action = parsed
        if version != job.version:
            await query.answer(STALE_TEXT)
            return
        if action != "v":
            await query.answer("Belum bisa, aksi ini masuk Fase 2")
            return
        await query.answer()
        text = (job.proposal or {}).get("ringkasan") or "(kosong)"
        for part in _chunks(text):
            await self.notifier.send(Outgoing(part))
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:  # pesan lama/terhapus: tidak masalah
            log.info("gagal hapus tombol job %s", job.id)

    async def on_done(self, job: Job, result: WorkerResult) -> None:
        """Dipanggil WorkerLoop setelah job selesai atau gagal final."""
        payload = job.payload or {}
        if not result.ok:
            await self.notifier.send(Outgoing(f"Job #{job.id} ({job.kind}) gagal: {result.error}"))
            return
        text = result.output.get("ringkasan", "")
        if job.kind == "digest":
            n = len(payload.get("item_ids", []))
            head = ("Pagi. " if payload.get("pagi") else "") + f"Digest {n} item:"
            text = f"{head}\n\n{text}"
        await self.notifier.send(
            Outgoing(text, job_id=job.id, version=job.version, reply_to=payload.get("message_id"))
        )


def build_application(
    config: Config, store: Store, notifier, wake: Callable[[], None]
) -> Application:
    """Application PTB dengan owner lock di group -1. Handlers ada di bot_data['handlers']."""
    app = Application.builder().token(config.telegram_bot_token).build()
    h = Handlers(config.owner_chat_id, store, notifier, wake)
    app.add_handler(TypeHandler(Update, h.owner_lock), group=-1)
    app.add_handler(CommandHandler("status", h.cmd_status))
    app.add_handler(CommandHandler("jobs", h.cmd_jobs))
    app.add_handler(CallbackQueryHandler(h.on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, h.on_text))
    app.bot_data["handlers"] = h
    return app
