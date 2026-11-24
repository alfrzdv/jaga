from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop, TypeHandler

from gateway import bot as b
from gateway.notifier import Outgoing
from gateway.store import Status, Store
from gateway.worker import WorkerResult

OWNER = 111
STRANGER = 999


class Sink:
    def __init__(self):
        self.sent: list[Outgoing] = []

    async def send(self, msg):
        self.sent.append(msg)


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answers = []
        self.markup_cleared = False

    async def answer(self, text=None, show_alert=None):
        self.answers.append(text)

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_cleared = reply_markup is None


def update(chat_id=OWNER, user_id=None, text=None, callback=None, message_id=10):
    user = SimpleNamespace(id=user_id if user_id is not None else chat_id)
    message = SimpleNamespace(text=text, message_id=message_id) if text is not None else None
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=user,
        effective_message=message,
        callback_query=FakeQuery(callback) if callback else None,
    )


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def h(store):
    sink = Sink()
    woke = []
    handlers = b.Handlers(OWNER, store, sink, wake=lambda: woke.append(1))
    return SimpleNamespace(h=handlers, sink=sink, woke=woke, store=store)


# --- callback data --------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("j:12:3:v", (12, 3, "v")),
        ("j:1:1:a", (1, 1, "a")),
        ("j:1:1:x", None),
        ("j:a:1:v", None),
        ("x:1:1:v", None),
        ("j:1:v", None),
        ("", None),
    ],
)
def test_parse_callback(data, expected):
    assert b.parse_callback(data) == expected


# --- owner lock -----------------------------------------------------------


async def test_owner_lock_passes_owner(h):
    await h.h.owner_lock(update(text="halo"), None)  # tidak raise


@pytest.mark.parametrize(
    "upd",
    [
        update(chat_id=STRANGER, text="halo"),
        update(chat_id=STRANGER, callback="j:1:1:v"),
        update(chat_id=OWNER, user_id=STRANGER, text="halo"),  # owner chat, user lain (grup)
        SimpleNamespace(effective_chat=None, effective_user=None),  # mis. update inline query
    ],
)
async def test_owner_lock_stops_others(h, upd):
    with pytest.raises(ApplicationHandlerStop):
        await h.h.owner_lock(upd, None)


def test_owner_lock_registered_first(make_config, store):
    app = b.build_application(make_config(owner_chat_id=OWNER), store, Sink(), wake=lambda: None)
    (guard,) = app.handlers[-1]
    assert isinstance(guard, TypeHandler)
    assert min(app.handlers) == -1


# --- chat -----------------------------------------------------------------


async def test_chat_creates_job_and_wakes(h):
    await h.h.on_text(update(text="cek lowongan dong", message_id=42), None)
    (job,) = h.store.list_jobs()
    assert job.kind == "chat" and job.source == "chat" and job.external_id == "42"
    assert job.payload == {"text": "cek lowongan dong", "message_id": 42}
    assert h.store.recent_chat(1)[0]["text"] == "cek lowongan dong"
    assert h.woke == [1]


async def test_same_message_twice_is_one_job(h):
    await h.h.on_text(update(text="halo", message_id=42), None)
    await h.h.on_text(update(text="halo", message_id=42), None)
    assert len(h.store.list_jobs()) == 1


# --- /status & /jobs ------------------------------------------------------


async def test_status_shows_queue_and_usage(h):
    j, _ = h.store.create_job("chat", "1", "chat", {"text": "x"})
    h.store.claim_next(["chat"])
    h.store.transition(j, Status.GAGAL, result="timeout")
    h.store.log_usage(j, "haiku", 2, 3000, 0.002)
    await h.h.cmd_status(update(text="/status"), None)
    text = h.sink.sent[-1].text
    assert "gagal: 1" in text and "timeout" in text and "chat" in text


async def test_jobs_lists_without_payload(h):
    h.store.create_job("web:lowongan", "i1", "item", {"title": "RAHASIA PAYLOAD"})
    await h.h.cmd_jobs(update(text="/jobs"), None)
    text = h.sink.sent[-1].text
    assert "web:lowongan" in text and "RAHASIA PAYLOAD" not in text


# --- callback -------------------------------------------------------------


def done_job(store, text="ringkasan lengkap"):
    j, _ = store.create_job("digest", "s1", "digest", {"item_ids": []})
    store.claim_next(["digest"])
    store.transition(j, Status.SELESAI, proposal={"ringkasan": text})
    return j


async def test_view_callback_sends_full_text(h):
    j = done_job(h.store, "x" * 6000)
    upd = update(callback=f"j:{j}:1:v")
    await h.h.on_callback(upd, None)
    q = upd.callback_query
    assert q.answers == [None]
    assert sum(len(m.text) for m in h.sink.sent) == 6000
    assert all(len(m.text) <= 4096 for m in h.sink.sent)
    assert q.markup_cleared


async def test_stale_version_answered(h):
    j = done_job(h.store)
    upd = update(callback=f"j:{j}:5:v")
    await h.h.on_callback(upd, None)
    assert upd.callback_query.answers == ["Draft udah berubah, pake pesan terbaru"]
    assert h.sink.sent == []


@pytest.mark.parametrize("data", ["j:999:1:v", "rusak", "j:1:1:a"])
async def test_other_callbacks_always_answered(h, data):
    done_job(h.store)
    upd = update(callback=data)
    await h.h.on_callback(upd, None)
    assert len(upd.callback_query.answers) == 1  # HP tidak loading terus
    assert h.sink.sent == []


# --- on_done --------------------------------------------------------------


def result(ok=True, text="jawaban", error=None):
    out = {"ringkasan": text, "draft": None, "aksi_diajukan": []} if ok else None
    return WorkerResult(ok, out, error, "haiku")


async def test_on_done_chat_replies_to_message(h):
    j, _ = h.store.create_job("chat", "42", "chat", {"text": "halo", "message_id": 42})
    await h.h.on_done(h.store.get_job(j), result(text="halo juga"))
    msg = h.sink.sent[-1]
    assert msg.text == "halo juga" and msg.reply_to == 42 and msg.job_id == j


async def test_on_done_digest(h):
    j, _ = h.store.create_job("digest", "s", "digest", {"item_ids": [1, 2, 3], "pagi": True})
    await h.h.on_done(h.store.get_job(j), result(text="- a\n- b\n- c"))
    text = h.sink.sent[-1].text
    assert text.startswith("Pagi. Digest 3 item")
    assert "- a" in text


async def test_on_done_failure_is_short(h):
    j, _ = h.store.create_job("chat", "42", "chat", {"text": "halo", "message_id": 42})
    await h.h.on_done(h.store.get_job(j), result(ok=False, error="timeout"))
    msg = h.sink.sent[-1]
    assert f"#{j}" in msg.text and "timeout" in msg.text


# --- integrasi dispatcher PTB ---------------------------------------------


def raw_update(update_id, chat_id, text=None, callback=None):
    user = {"id": chat_id, "is_bot": False, "first_name": "x"}
    chat = {"id": chat_id, "type": "private"}
    msg = {"message_id": update_id, "date": 0, "chat": chat, "from": user}
    if callback:
        query = {
            "id": str(update_id),
            "from": user,
            "chat_instance": "c",
            "data": callback,
            "message": msg | {"text": "lama"},
        }
        return {"update_id": update_id, "callback_query": query}
    return {"update_id": update_id, "message": msg | {"text": text}}


async def test_dispatcher_blocks_stranger_message_and_callback(make_config, store, monkeypatch):
    from telegram import Update as TgUpdate
    from telegram import User
    from telegram.ext import CallbackQueryHandler, ExtBot

    async def fake_get_me(self, *a, **kw):  # initialize() manggil getMe; jangan sentuh jaringan
        return User(id=1, is_bot=True, first_name="jaga", username="jaga_bot")

    monkeypatch.setattr(ExtBot, "get_me", fake_get_me)

    app = b.build_application(make_config(owner_chat_id=OWNER), store, Sink(), wake=lambda: None)
    hits = []

    async def fake_callback(update, context):
        hits.append(update.callback_query.data)

    (cb,) = [h for h in app.handlers[0] if isinstance(h, CallbackQueryHandler)]
    cb.callback = fake_callback
    await app.initialize()

    for raw in (raw_update(1, STRANGER, text="halo"), raw_update(2, STRANGER, callback="j:1:1:v")):
        await app.process_update(TgUpdate.de_json(raw, app.bot))
    assert store.list_jobs() == [] and hits == []

    await app.process_update(TgUpdate.de_json(raw_update(3, OWNER, text="halo"), app.bot))
    await app.process_update(TgUpdate.de_json(raw_update(4, OWNER, callback="j:1:1:v"), app.bot))
    assert len(store.list_jobs()) == 1 and hits == ["j:1:1:v"]
    await app.shutdown()
