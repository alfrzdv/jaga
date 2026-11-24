"""Antrean kirim Telegram + penjadwal digest (§7).

Notifier: satu-satunya jalur kirim pesan ke owner. Rate limit ~1 pesan/detik, jam tenang,
pesan > 4096 dipotong dengan tombol "Lihat", RetryAfter (429) ditunggu lalu dicoba lagi.

DigestScheduler: bikin job `digest` per slot waktu, dan heartbeat pagi yang selalu terkirim.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut

from .store import Store

log = logging.getLogger(__name__)

TELEGRAM_MAX = 4096
SEND_INTERVAL_S = 1.0
NETWORK_TRIES = 3
HEARTBEAT_CURSOR = "notifier:heartbeat"
SLOT_CURSOR = "notifier:digest_slot"


@dataclass
class Outgoing:
    text: str
    priority: str = "normal"  # 'urgent' tembus jam tenang
    job_id: int | None = None
    version: int | None = None
    reply_to: int | None = None  # message_id yang dibalas


def _local_now() -> datetime:
    return datetime.now().astimezone()


def in_quiet_hours(now: datetime, quiet: tuple[int, int]) -> bool:
    """quiet=(23, 6) artinya 23.00-05.59. start == end berarti tidak ada jam tenang."""
    start, end = quiet
    h = now.hour
    if start == end:
        return False
    if start < end:
        return start <= h < end
    return h >= start or h < end


def truncate(text: str, limit: int = TELEGRAM_MAX) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[: limit - 1] + "…", True


def _retry_seconds(exc: RetryAfter) -> float:
    ra = exc.retry_after
    return ra.total_seconds() if isinstance(ra, timedelta) else float(ra)


class Notifier:
    def __init__(
        self,
        bot,
        owner_chat_id: int,
        quiet_hours: tuple[int, int],
        clock: Callable[[], datetime] = _local_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        """bot: punya `async send_message(...)` (telegram.Bot atau palsu untuk tes)."""
        self.bot = bot
        self.owner = owner_chat_id
        self.quiet = quiet_hours
        self.clock = clock
        self.sleep = sleep
        self._queue: asyncio.Queue[Outgoing] = asyncio.Queue()
        self._held: list[Outgoing] = []

    def _quiet_now(self) -> bool:
        return in_quiet_hours(self.clock(), self.quiet)

    async def send(self, msg: Outgoing) -> None:
        if msg.priority != "urgent" and self._quiet_now():
            self._held.append(msg)
        else:
            await self._queue.put(msg)

    async def _deliver(self, msg: Outgoing) -> None:
        text, cut = truncate(msg.text)
        markup = None
        if cut and msg.job_id is not None:
            data = f"j:{msg.job_id}:{msg.version or 1}:v"
            markup = InlineKeyboardMarkup([[InlineKeyboardButton("Lihat", callback_data=data)]])
        network_fails = 0
        while True:
            try:
                await self.bot.send_message(
                    chat_id=self.owner,
                    text=text,
                    reply_markup=markup,
                    reply_to_message_id=msg.reply_to,
                )
                return
            except RetryAfter as e:
                await self.sleep(_retry_seconds(e))
            except NetworkError, TimedOut:
                network_fails += 1
                if network_fails >= NETWORK_TRIES:
                    log.error("kirim gagal %dx (jaringan), pesan dibuang", network_fails)
                    return
                await self.sleep(5 * network_fails)
            except TelegramError as e:
                log.error("kirim ditolak Telegram: %s", type(e).__name__)
                return

    async def step(self) -> bool:
        """Lepas pesan tahanan kalau jam tenang selesai, lalu kirim satu pesan. False = kosong."""
        if self._held and not self._quiet_now():
            for msg in self._held:
                self._queue.put_nowait(msg)
            self._held.clear()
        try:
            msg = self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return False
        await self._deliver(msg)
        return True

    async def run(self) -> None:
        while True:
            try:
                sent = await self.step()
            except Exception:
                log.exception("notifier error")
                sent = False
            if sent:
                await self.sleep(SEND_INTERVAL_S)
                continue
            try:  # tunggu pesan baru, tapi bangun tiap 30 detik untuk cek tahanan jam tenang
                msg = await asyncio.wait_for(self._queue.get(), 30)
                self._queue.put_nowait(msg)
            except TimeoutError:
                pass


class DigestScheduler:
    def __init__(
        self,
        store: Store,
        notifier,
        wake: Callable[[], None],
        every_hours: int,
        morning_hour: int,
        quiet_hours: tuple[int, int],
        clock: Callable[[], datetime] = _local_now,
    ):
        self.store = store
        self.notifier = notifier
        self.wake = wake
        self.every = max(1, every_hours)
        self.morning = morning_hour
        self.quiet = quiet_hours
        self.clock = clock

    def _cursor(self, name: str) -> str | None:
        cur = self.store.get_cursor(name)
        return cur["last_checked"] if cur else None

    def mark_heartbeat_done(self) -> None:
        self.store.put_cursor(HEARTBEAT_CURSOR, self.clock().date().isoformat())

    def _create_digest(self, external_id: str, pagi: bool) -> bool:
        items = self.store.pending_items()
        if not items or self.store.has_open_digest():
            return False
        payload = {"item_ids": [i.id for i in items], "pagi": pagi}
        _, created = self.store.create_job("digest", external_id, "digest", payload)
        if created:
            self.wake()
        return created

    async def tick(self) -> None:
        now = self.clock()
        today = now.date().isoformat()

        if now.hour >= self.morning and self._cursor(HEARTBEAT_CURSOR) != today:
            # heartbeat: hasil digest pagi, atau pesan "aman" kalau tidak ada yang bisa diringkas
            if not self._create_digest(f"pagi:{today}", pagi=True):
                if self.store.has_open_digest():
                    text = "Pagi. Digest sebelumnya masih diproses, hasilnya nyusul."
                else:
                    text = "Pagi. Aman, 0 item baru."
                await self.notifier.send(Outgoing(text))
            self.mark_heartbeat_done()
            return

        if in_quiet_hours(now, self.quiet):
            return  # item nunggu digest pagi; hasilnya toh bakal ditahan
        slot = f"{today}T{now.hour // self.every * self.every:02d}"
        if self._cursor(SLOT_CURSOR) != slot and self._create_digest(slot, pagi=False):
            self.store.put_cursor(SLOT_CURSOR, slot)

    async def run(self, interval_s: float = 60) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("digest scheduler error")
            await asyncio.sleep(interval_s)
