"""Antrean kirim Telegram: rate limit, jam tenang, digest, heartbeat (§7)."""

from dataclasses import dataclass
from datetime import datetime

TELEGRAM_MAX = 4096


@dataclass
class Outgoing:
    text: str
    priority: str = "normal"  # 'urgent' tembus jam tenang
    job_id: int | None = None
    version: int | None = None
    buttons: list[tuple[str, str]] | None = None  # [(label, callback_data)]


def in_quiet_hours(now: datetime, quiet: tuple[int, int]) -> bool:
    """quiet=(23, 6) artinya 23.00-05.59. Tangani rentang yang lewat tengah malam."""
    raise NotImplementedError


def truncate(text: str, limit: int = TELEGRAM_MAX) -> tuple[str, bool]:
    """Potong ke limit (sisakan ruang untuk penanda). Return (teks, terpotong)."""
    raise NotImplementedError


class Notifier:
    def __init__(
        self,
        bot,
        owner_chat_id: int,
        quiet_hours: tuple[int, int],
        digest_every_hours: int,
        morning_digest_hour: int,
        clock=datetime.now,
    ):
        """bot = telegram.Bot (atau fake untuk tes). clock bisa di-inject untuk tes."""
        # TODO: asyncio.Queue untuk kirim langsung, list buffer digest, list tahanan jam tenang
        raise NotImplementedError

    async def send(self, msg: Outgoing) -> None:
        """Masuk antrean. Kalau jam tenang dan bukan urgent → ditahan sampai jam tenang selesai."""
        raise NotImplementedError

    def add_to_digest(self, line: str) -> None:
        """Item D: ditampung, dikirim saat digest berikutnya."""
        raise NotImplementedError

    async def run(self) -> None:
        """Loop utama (task asyncio).

        TODO:
        - kirim dari antrean maks ~1 pesan/detik
        - RetryAfter (429) → tidur sesuai retry_after lalu coba lagi pesan yang sama
        - pesan > 4096 → truncate + tombol "Lihat" (callback j:<id>:<v>:v)
        - tiap digest_every_hours: kirim buffer digest kalau ada isinya
        - jam morning_digest_hour: SELALU kirim digest pagi (heartbeat), walau kosong
          ("aman, 0 item baru"); simpan tanggal terakhir supaya tidak dobel
        - setelah jam tenang selesai: lepas pesan yang ditahan
        """
        raise NotImplementedError
