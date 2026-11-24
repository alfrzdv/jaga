from datetime import UTC, datetime, timedelta

import pytest

from gateway.context import build_chat_context, pick_sources
from gateway.store import Store


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 24, tzinfo=UTC)

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def store():
    s = Store(":memory:", clock=Clock())
    yield s
    s.close()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("cek lowongan dong", ["web:lowongan"]),
        ("ada MAGANG baru?", ["web:lowongan"]),
        ("lowongan magang apa aja", ["web:lowongan"]),
        ("halo", []),
    ],
)
def test_pick_sources(text, expected):
    assert pick_sources(text) == expected


def test_context_has_history_and_items(store):
    store.add_chat("user", "pagi")
    store.add_chat("assistant", "pagi juga")
    for n in range(3):
        store.create_job("web:lowongan", f"i{n}", "item", {"title": f"Magang {n}"})
    store.create_job("web:berita", "b1", "item", {"title": "Berita ga relevan"})
    store.add_chat("user", "cek lowongan dong")  # pesan sekarang, sudah disimpan bot

    ctx = build_chat_context(store, "cek lowongan dong")
    assert "owner: pagi" in ctx and "jaga: pagi juga" in ctx
    assert ctx.count("cek lowongan dong") == 0  # pesan sekarang tidak diulang di riwayat
    assert "Magang 0" in ctx and "Magang 2" in ctx
    assert ctx.index("Magang 2") < ctx.index("Magang 0")  # terbaru dulu
    assert "Berita ga relevan" not in ctx


def test_item_fields_included(store):
    store.create_job("web:lowongan", "i1", "item", {"title": "Magang A", "link": "https://x"})
    ctx = build_chat_context(store, "lowongan?")
    assert "Magang A" in ctx and "https://x" in ctx


def test_purged_payload_skipped(store):
    j, _ = store.create_job("web:lowongan", "i1", "item", {"title": "lama"})
    store.conn.execute("UPDATE jobs SET payload = NULL WHERE id = ?", (j,))
    store.create_job("web:lowongan", "i2", "item", {"title": "baru"})
    ctx = build_chat_context(store, "lowongan")
    assert "baru" in ctx and "lama" not in ctx


def test_context_length_capped(store):
    for n in range(20):
        store.create_job("web:lowongan", f"i{n}", "item", {"title": "x" * 5000})
    assert len(build_chat_context(store, "lowongan", max_chars=8000)) <= 8000


def test_no_topic_no_items(store):
    store.create_job("web:lowongan", "i1", "item", {"title": "Magang A"})
    assert "Magang A" not in build_chat_context(store, "halo")
