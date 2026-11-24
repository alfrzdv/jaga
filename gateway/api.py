"""HTTP API untuk n8n. Bind 127.0.0.1 saja (§4.5, §8.6)."""

from fastapi import FastAPI
from pydantic import BaseModel

from .config import Config
from .notifier import Notifier
from .store import Store


class JobIn(BaseModel):
    source: str
    external_id: str
    kind: str  # Fase 1: cuma 'summarize'; kind lain ditolak 422
    payload: dict
    priority: str = "normal"


class CursorIn(BaseModel):
    last_checked: str
    meta: dict | None = None


class AlertIn(BaseModel):
    key: str
    message: str


def create_app(config: Config, store: Store, notifier: Notifier) -> FastAPI:
    """TODO:
    - middleware: tolak 403 kalau X-Gateway-Token salah (compare_digest),
      tolak 400 kalau Host bukan 127.0.0.1:<port> / localhost:<port> (DNS rebinding)
    - POST /jobs → 201 {id} kalau baru, 200 {duplicate: true, id} kalau sudah ada;
      setelah create, bangunkan worker loop (asyncio.Event)
    - GET /cursor/{source} → 200 {last_checked, meta} / 404
    - PUT /cursor/{source} → 204
    - POST /alerts → dedupe per key via store.should_send_alert, lalu notifier.send
    - GET /health → 200, tetap wajib token
    - matikan /docs dan /openapi.json
    """
    raise NotImplementedError
