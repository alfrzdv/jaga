from datetime import datetime, timedelta

import pytest
from telegram.error import NetworkError, RetryAfter

from gateway.notifier import (
    TELEGRAM_MAX,
    DigestScheduler,
    Notifier,
    Outgoing,
    in_quiet_hours,
    truncate,
)
from gateway.store import Status, Store

OWNER = 111


class Clock:
    def __init__(self, hour=12, minute=0):
        self.now = datetime(2026, 11, 24, hour, minute).astimezone()

    def __call__(self):
        return self.now

    def set(self, hour, minute=0, days=0):
        base = datetime(2026, 11, 24, hour, minute) + timedelta(days=days)
        self.now = base.astimezone()


class FakeBot:
    def __init__(self, fail_with=None):
        self.sent = []
        self.fail_with = list(fail_with or [])

    async def send_message(self, chat_id, text, reply_markup=None, reply_to_message_id=None):
        if self.fail_with:
            raise self.fail_with.pop(0)
        self.sent.append(
            {
                "chat_id": chat_id,
                "text": text,
                "markup": reply_markup,
                "reply_to": reply_to_message_id,
            }
        )


async def no_sleep(_s):
    return None


def make_notifier(bot, clock, quiet=(23, 6)):
    return Notifier(bot, OWNER, quiet, clock=clock, sleep=no_sleep)


# --- helper ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("quiet", "hour", "minute", "expected"),
    [
        ((23, 6), 23, 0, True),
        ((23, 6), 2, 0, True),
        ((23, 6), 5, 59, True),
        ((23, 6), 6, 0, False),
        ((23, 6), 22, 59, False),
        ((1, 5), 3, 0, True),
        ((1, 5), 0, 30, False),
        ((0, 0), 3, 0, False),  # start == end: tanpa jam tenang
    ],
)
def test_in_quiet_hours(quiet, hour, minute, expected):
    assert in_quiet_hours(datetime(2026, 11, 24, hour, minute), quiet) is expected


def test_truncate():
    assert truncate("pendek") == ("pendek", False)
    text, cut = truncate("x" * 5000)
    assert cut and len(text) <= TELEGRAM_MAX and text.endswith("…")


# --- Notifier -------------------------------------------------------------


async def test_send_delivers_to_owner():
    bot, clock = FakeBot(), Clock(12)
    n = make_notifier(bot, clock)
    await n.send(Outgoing("halo", reply_to=5))
    assert await n.step()
    assert bot.sent == [{"chat_id": OWNER, "text": "halo", "markup": None, "reply_to": 5}]
    assert not await n.step()  # antrean kosong


async def test_quiet_hours_hold_normal_but_not_urgent():
    bot, clock = FakeBot(), Clock(23, 30)
    n = make_notifier(bot, clock)
    await n.send(Outgoing("biasa"))
    await n.send(Outgoing("darurat", priority="urgent"))
    while await n.step():
        pass
    assert [m["text"] for m in bot.sent] == ["darurat"]

    clock.set(6, 5, days=1)
    while await n.step():
        pass
    assert [m["text"] for m in bot.sent] == ["darurat", "biasa"]


async def test_long_message_gets_view_button():
    bot = FakeBot()
    n = make_notifier(bot, Clock(12))
    await n.send(Outgoing("x" * 5000, job_id=7, version=2))
    await n.step()
    msg = bot.sent[0]
    assert len(msg["text"]) <= TELEGRAM_MAX
    button = msg["markup"].inline_keyboard[0][0]
    assert button.text == "Lihat" and button.callback_data == "j:7:2:v"


async def test_long_message_without_job_has_no_button():
    bot = FakeBot()
    n = make_notifier(bot, Clock(12))
    await n.send(Outgoing("x" * 5000))
    await n.step()
    assert bot.sent[0]["markup"] is None


async def test_retry_after_then_success():
    bot = FakeBot(fail_with=[RetryAfter(3), RetryAfter(timedelta(seconds=1))])
    slept = []

    async def sleep(s):
        slept.append(s)

    n = Notifier(bot, OWNER, (23, 6), clock=Clock(12), sleep=sleep)
    await n.send(Outgoing("halo"))
    await n.step()
    assert [m["text"] for m in bot.sent] == ["halo"]
    assert slept[:2] == [3.0, 1.0]


async def test_network_error_gives_up_after_retries():
    bot = FakeBot(fail_with=[NetworkError("putus")] * 10)
    n = make_notifier(bot, Clock(12))
    await n.send(Outgoing("halo"))
    assert await n.step()  # tidak crash; pesan dibuang setelah beberapa kali coba
    assert bot.sent == []
    assert not await n.step()


# --- DigestScheduler ------------------------------------------------------


class Sink:
    def __init__(self):
        self.sent = []
        self.woke = 0

    async def send(self, msg):
        self.sent.append(msg)

    def wake(self):
        self.woke += 1


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def scheduler(store, clock, sink, every=6, morning=7, quiet=(23, 6)):
    return DigestScheduler(store, sink, sink.wake, every, morning, quiet, clock=clock)


def add_items(store, n, prefix="i"):
    return [store.create_job("web:lowongan", f"{prefix}{k}", "item", {"t": k})[0] for k in range(n)]


def digests(store):
    return [j for j in store.list_jobs(limit=100) if j.kind == "digest"]


async def test_regular_digest_once_per_slot(store):
    clock, sink = Clock(12, 10), Sink()
    s = scheduler(store, clock, sink)
    s.mark_heartbeat_done()  # fokus ke digest reguler
    ids = add_items(store, 2)
    await s.tick()
    await s.tick()
    (d,) = digests(store)
    assert d.payload == {"item_ids": ids, "pagi": False}
    assert sink.woke == 1

    # digest masih terbuka: slot berikutnya tidak bikin digest baru
    add_items(store, 1, prefix="j")
    clock.set(18, 5)
    await s.tick()
    assert len(digests(store)) == 1

    store.claim_next(["digest"])
    store.transition(d.id, Status.SELESAI, proposal={"ringkasan": "x"})
    store.finish_items(ids, "digest")
    await s.tick()
    assert len(digests(store)) == 2


async def test_no_items_no_regular_digest(store):
    clock, sink = Clock(12), Sink()
    s = scheduler(store, clock, sink)
    s.mark_heartbeat_done()
    await s.tick()
    assert digests(store) == [] and sink.sent == []


async def test_no_regular_digest_during_quiet_hours(store):
    clock, sink = Clock(0, 10), Sink()
    s = scheduler(store, clock, sink)
    add_items(store, 2)
    await s.tick()
    assert digests(store) == []


async def test_morning_heartbeat_when_empty_once_per_day(store):
    clock, sink = Clock(6, 30), Sink()
    s = scheduler(store, clock, sink)
    await s.tick()
    assert sink.sent == []  # belum jam 7

    clock.set(7, 0)
    await s.tick()
    await s.tick()
    assert len(sink.sent) == 1
    assert "0 item baru" in sink.sent[0].text

    clock.set(7, 0, days=1)
    await s.tick()
    assert len(sink.sent) == 2


async def test_morning_with_items_creates_morning_digest(store):
    clock, sink = Clock(7, 1), Sink()
    s = scheduler(store, clock, sink)
    ids = add_items(store, 3)
    await s.tick()
    (d,) = digests(store)
    assert d.payload == {"item_ids": ids, "pagi": True}
    assert sink.sent == []  # hasil digest yang jadi heartbeat


async def test_morning_with_open_digest_still_sends_heartbeat(store):
    clock, sink = Clock(7, 1), Sink()
    s = scheduler(store, clock, sink)
    ids = add_items(store, 1)
    store.create_job("digest", "lama", "digest", {"item_ids": ids, "pagi": False})
    await s.tick()
    assert len(sink.sent) == 1 and "masih diproses" in sink.sent[0].text


async def test_heartbeat_state_survives_restart(store):
    clock, sink = Clock(7, 1), Sink()
    await scheduler(store, clock, sink).tick()
    sink2 = Sink()
    await scheduler(store, clock, sink2).tick()
    assert len(sink.sent) == 1 and sink2.sent == []
