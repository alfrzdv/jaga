from datetime import UTC, datetime, timedelta

import pytest

from gateway.store import InvalidTransition, StaleVersion, Status, Store


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 24, 3, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(clock):
    s = Store(":memory:", clock=clock)
    yield s
    s.close()


def new_job(store, ext="1", kind="chat", source="chat", **kw):
    job_id, created = store.create_job(source, ext, kind, {"text": "halo"}, **kw)
    assert created
    return job_id


# --- schema & dedupe ------------------------------------------------------


def test_wal_and_version(tmp_path, clock):
    s = Store(tmp_path / "t.db", clock=clock)
    assert s.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert s.conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert s.sqlite_version_ok()
    s.close()
    # buka ulang: migrasi tidak jalan dua kali
    s = Store(tmp_path / "t.db", clock=clock)
    assert s.conn.execute("PRAGMA user_version").fetchone()[0] == 1
    s.close()


def test_create_job_dedupe(store):
    a, created_a = store.create_job("web:lowongan", "x1", "item", {"title": "A"})
    b, created_b = store.create_job("web:lowongan", "x1", "item", {"title": "beda"})
    assert created_a and not created_b
    assert a == b
    job = store.get_job(a)
    assert job.payload == {"title": "A"}
    assert job.status is Status.BARU
    assert job.version == 1 and job.attempts == 0


def test_same_external_id_other_source_is_new(store):
    _, c1 = store.create_job("web:a", "1", "item", {})
    _, c2 = store.create_job("web:b", "1", "item", {})
    assert c1 and c2


# --- claim ----------------------------------------------------------------


def test_claim_next_urgent_first_and_kind_filter(store):
    store.create_job("web:x", "i1", "item", {})
    normal = new_job(store, "1")
    urgent = new_job(store, "2", priority="urgent")
    first = store.claim_next(["chat"])
    assert first.id == urgent and first.status is Status.DIPROSES and first.attempts == 1
    assert store.claim_next(["chat"]).id == normal
    assert store.claim_next(["chat"]) is None  # item tidak pernah di-claim per item


# --- transisi -------------------------------------------------------------


def test_happy_path_no_action(store):
    j = new_job(store)
    store.claim_next(["chat"])
    job = store.transition(j, Status.SELESAI, proposal={"ringkasan": "ok"})
    assert job.status is Status.SELESAI
    assert job.proposal == {"ringkasan": "ok"}
    assert job.version == 1  # proposal pertama tidak menaikkan version


@pytest.mark.parametrize("start", [Status.BARU, Status.DIPROSES])
def test_no_auto_path_to_disetujui(store, start):
    j = new_job(store)
    if start is Status.DIPROSES:
        store.claim_next(["chat"])
    with pytest.raises(InvalidTransition):
        store.transition(j, Status.DISETUJUI)


def test_terminal_states_are_final(store):
    j = new_job(store)
    store.claim_next(["chat"])
    store.transition(j, Status.GAGAL, result="timeout")
    with pytest.raises(InvalidTransition):
        store.transition(j, Status.BARU)


def test_edit_bumps_version_and_rejects_stale(store):
    j = new_job(store, kind="draft_reply", source="gmail")
    store.claim_next(["draft_reply"])
    store.transition(j, Status.NUNGGU_APPROVE, proposal={"draft": "v1"})
    job = store.transition(j, Status.NUNGGU_APPROVE, expected_version=1, proposal={"draft": "v2"})
    assert job.version == 2
    with pytest.raises(StaleVersion):
        store.transition(j, Status.DISETUJUI, expected_version=1)
    assert store.transition(j, Status.DISETUJUI, expected_version=2).status is Status.DISETUJUI


def test_retry_back_to_baru(store):
    j = new_job(store)
    store.claim_next(["chat"])
    store.transition(j, Status.BARU, result="timeout")
    job = store.claim_next(["chat"])
    assert job.id == j and job.attempts == 2


def test_transition_missing_job(store):
    with pytest.raises(KeyError):
        store.transition(999, Status.DIPROSES)


# --- recover --------------------------------------------------------------


def test_recover_on_start(store, clock):
    stale = new_job(store, "1")
    store.claim_next(["chat"])
    clock.advance(minutes=30)
    fresh = new_job(store, "2")
    store.claim_next(["chat"])

    stuck = new_job(store, "3", kind="draft_reply", source="gmail")
    store.claim_next(["draft_reply"])
    store.transition(stuck, Status.NUNGGU_APPROVE, proposal={"draft": "x"})
    store.transition(stuck, Status.DISETUJUI, expected_version=1)

    counts = store.recover_on_start(stale_minutes=15)
    assert counts == {"reset": 1, "perlu_cek": 1}
    assert store.get_job(stale).status is Status.BARU
    assert store.get_job(fresh).status is Status.DIPROSES
    assert store.get_job(stuck).status is Status.PERLU_CEK


# --- item & digest --------------------------------------------------------


def test_items_and_digest(store):
    ids = [store.create_job("web:lowongan", f"i{n}", "item", {"n": n})[0] for n in range(3)]
    new_job(store, "c1")  # chat, bukan item
    assert [j.id for j in store.pending_items()] == ids
    assert not store.has_open_digest()

    d, _ = store.create_job("digest", "2026-11-24T07", "digest", {"item_ids": ids})
    assert store.has_open_digest()
    store.claim_next(["digest"])
    assert store.has_open_digest()

    assert store.finish_items(ids, result=f"digest:{d}") == 3
    store.transition(d, Status.SELESAI, proposal={"ringkasan": "3 lowongan"})
    assert not store.has_open_digest()
    assert store.pending_items() == []
    assert store.get_job(ids[0]).status is Status.SELESAI
    # finish_items cuma menyentuh item yang masih baru
    assert store.finish_items(ids, result="lagi") == 0


def test_recent_by_source(store, clock):
    for n in range(5):
        store.create_job("web:lowongan", f"i{n}", "item", {"n": n})
        clock.advance(minutes=1)
    store.create_job("web:berita", "b", "item", {})
    got = store.recent_by_source("web:lowongan", limit=3)
    assert [j.payload["n"] for j in got] == [4, 3, 2]


# --- cursor, chat, alert --------------------------------------------------


def test_cursor_roundtrip(store):
    assert store.get_cursor("web:lowongan") is None
    store.put_cursor("web:lowongan", "2026-11-24T00:00:00Z", {"etag": "abc"})
    store.put_cursor("web:lowongan", "2026-11-24T01:00:00Z", {"etag": "def"})
    assert store.get_cursor("web:lowongan") == {
        "last_checked": "2026-11-24T01:00:00Z",
        "meta": {"etag": "def"},
    }


def test_chat_history_order(store, clock):
    for n in range(4):
        store.add_chat("user" if n % 2 == 0 else "assistant", f"pesan {n}")
        clock.advance(seconds=1)
    assert [m["text"] for m in store.recent_chat(3)] == ["pesan 1", "pesan 2", "pesan 3"]


def test_alert_dedupe(store, clock):
    assert store.should_send_alert("gmail-auth", min_interval_s=3600)
    assert not store.should_send_alert("gmail-auth", min_interval_s=3600)
    clock.advance(hours=2)
    assert store.should_send_alert("gmail-auth", min_interval_s=3600)
    row = store.conn.execute("SELECT count FROM alerts WHERE key='gmail-auth'").fetchone()
    assert row[0] == 3


# --- usage ----------------------------------------------------------------


def test_usage_today_per_kind(store, clock):
    c = new_job(store, "1")
    store.log_usage(c, "haiku", 2, 2000, 0.001)
    store.log_usage(c, "haiku", None, None, None)  # timeout: usage tidak diketahui
    old_clock = clock.now
    clock.now = old_clock - timedelta(days=2)
    store.log_usage(c, "haiku", 2, 1000, 0.5)
    clock.now = old_clock
    usage = store.usage_today()
    assert usage["chat"]["calls"] == 2
    assert usage["chat"]["duration_ms"] == 2000
    assert usage["chat"]["cost_usd"] == pytest.approx(0.001)
    assert store.calls_today("chat") == 2
    assert store.calls_today("summarize") == 0


# --- retensi --------------------------------------------------------------


def test_purge_old_keeps_actions_log(store, clock):
    old = new_job(store, "old")
    store.add_chat("user", "lama")
    store.conn.execute(
        "INSERT INTO actions_log(job_id, version, action_type, params, outcome)"
        " VALUES (?, 1, 'send_email', '{}', 'ok')",
        (old,),
    )
    clock.advance(days=31)
    new = new_job(store, "new")
    store.add_chat("user", "baru")

    counts = store.purge_old(days=30)
    assert counts == {"payload": 1, "chat": 1}
    assert store.get_job(old).payload is None
    assert store.get_job(new).payload == {"text": "halo"}
    assert [m["text"] for m in store.recent_chat(10)] == ["baru"]
    assert store.conn.execute("SELECT COUNT(*) FROM actions_log").fetchone()[0] == 1


# --- status ---------------------------------------------------------------


def test_status_summary(store):
    new_job(store, "1")
    j = new_job(store, "2")
    store.claim_next(["chat"])
    store.claim_next(["chat"])
    store.transition(j, Status.GAGAL, result="structured_output hilang")
    s = store.status_summary()
    assert s["counts"] == {"diproses": 1, "gagal": 1}
    assert s["last_error"]["job_id"] == j
    assert s["last_error"]["result"] == "structured_output hilang"
    assert "usage_today" in s


def test_count_waiting(store):
    new_job(store, "1")
    new_job(store, "2")
    store.claim_next(["chat"])
    assert store.count_waiting("chat") == 1
    assert store.count_waiting("digest") == 0


def test_transaction_rolls_back_on_error(store):
    j = new_job(store)
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.conn.execute("UPDATE jobs SET status = 'gagal' WHERE id = ?", (j,))
            raise RuntimeError("mati di tengah")
    assert store.get_job(j).status is Status.BARU


def test_release_returns_job_without_attempt(store):
    j = new_job(store)
    store.claim_next(["chat"])
    job = store.release(j, result="auth")
    assert job.status is Status.BARU and job.attempts == 0 and job.result == "auth"
    with pytest.raises(InvalidTransition):
        store.release(j, result="lagi")  # sudah baru
