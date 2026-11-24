"""Handler Telegram: owner lock, perintah, chat, tombol (§4.7, §8.4)."""

from telegram.ext import Application

from .config import Config
from .store import Store
from .worker import WorkerLoop


def parse_callback(data: str) -> tuple[int, int, str] | None:
    """'j:<job_id>:<version>:<aksi>' → (job_id, version, aksi). None kalau format salah."""
    raise NotImplementedError


def build_application(config: Config, store: Store, worker: WorkerLoop) -> Application:
    """TODO:
    - Application.builder().token(...).build()
    - owner lock: TypeHandler(Update, cek) di group=-1; kalau effective_chat/effective_user
      bukan OWNER_CHAT_ID → raise ApplicationHandlerStop. Menutup message DAN callback_query.
    - /status → store.status_summary(): antrean per status, error terakhir, usage hari ini
    - /jobs → 10 job terakhir (id, source, status), tanpa isi payload
    - teks biasa → store.add_chat('user'), buat job kind='chat' source='chat'
      external_id=<message_id>, worker.wake(); jawaban dikirim lewat on_done
    - callback 'v' (lihat) → kirim ringkasan lengkap job. Fase 1 cuma 'v';
      a/e/t masuk Fase 2. SELALU answerCallbackQuery, termasuk saat versi basi
    """
    raise NotImplementedError
