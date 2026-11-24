# Rencana tes Fase 1

Ditulis bareng implementasi tiap modul (test dulu, lalu kode).

- `test_config.py`: key wajib hilang → error menyebut nama key, nilai tidak ikut ke pesan; JAGA_HOME di OneDrive ditolak; parse DAILY_LIMITS dan QUIET_HOURS.
- `test_store.py` (`:memory:`): dedupe `UNIQUE(source, external_id)`; transisi sah/tidak sah; tidak ada jalur ke `disetujui` dari `baru`/`diproses`; version naik saat proposal berubah; `StaleVersion`; `recover_on_start` (diproses lama → baru, disetujui/dieksekusi → perlu_cek); `purge_old` tidak menyentuh `actions_log`; `claim_next` urutan urgent dulu.
- `test_api.py` (TestClient): token salah → 403; Host asing → 400; POST /jobs 201 lalu 200 duplicate; cursor GET/PUT; alerts di-dedupe.
- `test_worker.py` (fake_claude): argv berisi flag isolasi dan `--tools ""`; env tanpa `ANTHROPIC_API_KEY`; semua mode fake_claude → ok/gagal yang benar; timeout → gagal tanpa usage; retry 2x lalu `gagal`; batas harian.
- `test_bot.py`: owner lock menghentikan message **dan** callback_query dari chat lain; parse_callback; tombol versi basi tetap di-answer.
- `test_notifier.py` (clock palsu, bot palsu): jam tenang lewat tengah malam; urgent tembus; truncate 4096; heartbeat pagi sekali per hari walau kosong; 429 → tunggu lalu kirim ulang.
- `test_context.py`: kata kunci → source; payload NULL dilewati; batas panjang.
