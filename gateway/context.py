"""Ambil data relevan dari SQLite untuk prompt chat (§5.4-5.5). Worker tidak akses DB."""

from .store import Store

# kata kunci → prefix source. Sederhana dulu; tambah saat sumber baru masuk.
TOPICS: dict[str, str] = {
    "lowongan": "web:lowongan",
    "magang": "web:lowongan",
}

CHAT_HISTORY_N = 10
ITEMS_N = 20


def pick_sources(text: str) -> list[str]:
    """Cocokkan kata kunci (case-insensitive) → daftar source unik."""
    raise NotImplementedError


def build_chat_context(store: Store, text: str) -> str:
    """Gabung N chat terakhir + item relevan jadi teks untuk prompt.

    TODO:
    - item: judul/ringkasan dari proposal kalau ada, kalau tidak dari payload
    - payload yang sudah di-purge (NULL) dilewati
    - batasi panjang total (mis. 30k karakter) supaya prompt tidak bengkak
    """
    raise NotImplementedError
