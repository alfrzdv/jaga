# Tes yang belum ada

Tes untuk config, store, worker, api, context, notifier, dan bot sudah ditulis (`test_*.py`). Yang belum:

- `main.py`: start lalu stop tanpa error dengan fake bot dan fake claude; `recover_on_start` dan `install_worker_home` terpanggil; uvicorn cuma bind `127.0.0.1`.
- Watcher n8n: workflow dites pakai pinned sample data (§11). Duplikat RSS item → `POST /jobs` dibalas `duplicate: true`.
- End-to-end manual: checklist "Selesai kalau" Fase 1 di CLAUDE.md §9.
