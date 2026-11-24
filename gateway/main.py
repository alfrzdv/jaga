"""Entrypoint: PTB + uvicorn + worker loop + notifier dalam satu event loop.

Urutan start mengikuti Application.run_polling di PTB 22.8 (lihat findings):
  app.initialize() → app.updater.start_polling() → app.start()
Urutan stop:
  app.updater.stop() → app.stop() → app.shutdown()
"""

import asyncio


async def amain() -> None:
    """TODO:
    1. config.load(); setup logging ke JAGA_HOME/logs (rotating, tanpa token/isi email)
    2. store = Store(config.db_path); warning kalau sqlite_version_ok() False
    3. store.recover_on_start(config.stale_minutes) → log hitungan
    4. siapkan worker_dir: copy worker_home/ kalau belum ada / versi beda; pastikan
       claude_config_dir ada
    5. bangun Notifier, WorkerLoop, Application (bot), FastAPI app (api)
    6. uvicorn.Server(Config(app, host='127.0.0.1', port=..., log_config=None)).serve()
       sebagai task
    7. task: notifier.run(), worker.run(), retensi harian (store.purge_old)
    8. tunggu sinyal stop (Ctrl+C / Task Scheduler stop) → matikan berurutan
    """
    raise NotImplementedError


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
