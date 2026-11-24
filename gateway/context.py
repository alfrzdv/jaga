"""Ambil data relevan dari SQLite untuk prompt chat (§5.4-5.5). Worker tidak akses DB.

Hasilnya masuk ke blok <data> di prompt: riwayat chat juga diperlakukan sebagai data, karena
jawaban Worker sebelumnya bisa saja memuat teks dari sumber luar.
"""

import json

from .store import Store

# kata kunci → source. Sederhana dulu; tambah saat sumber baru masuk.
TOPICS: dict[str, str] = {
    "lowongan": "web:lowongan",
    "magang": "web:lowongan",
    "loker": "web:lowongan",
}

CHAT_HISTORY_N = 10
ITEMS_N = 20
ITEM_MAX_CHARS = 1000
MAX_CHARS = 30_000
ROLE = {"user": "owner", "assistant": "jaga"}


def pick_sources(text: str) -> list[str]:
    """Cocokkan kata kunci (case-insensitive) → daftar source unik, urut kemunculan di TOPICS."""
    low = text.lower()
    out: list[str] = []
    for word, source in TOPICS.items():
        if word in low and source not in out:
            out.append(source)
    return out


def _history(store: Store, text: str) -> list[str]:
    msgs = store.recent_chat(CHAT_HISTORY_N + 1)
    # bot menyimpan pesan sekarang sebelum bikin job; jangan diulang di riwayat
    if msgs and msgs[-1]["role"] == "user" and msgs[-1]["text"] == text:
        msgs = msgs[:-1]
    return [f"{ROLE.get(m['role'], m['role'])}: {m['text']}" for m in msgs[-CHAT_HISTORY_N:]]


def _item_line(payload: dict) -> str:
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) > ITEM_MAX_CHARS:
        text = text[:ITEM_MAX_CHARS] + "…"
    return f"- {text}"


def build_chat_context(store: Store, text: str, max_chars: int = MAX_CHARS) -> str:
    """Riwayat chat + item relevan (terbaru dulu), dipotong ke max_chars."""
    sections: list[str] = []
    history = _history(store, text)
    if history:
        sections.append("[riwayat chat]\n" + "\n".join(history))
    for source in pick_sources(text):
        lines = [
            _item_line(j.payload) for j in store.recent_by_source(source, ITEMS_N) if j.payload
        ]
        if lines:
            sections.append(f"[{source}, {len(lines)} item terbaru]\n" + "\n".join(lines))
    out = "\n\n".join(sections)
    if len(out) > max_chars:
        out = out[: max_chars - 1] + "…"
    return out
