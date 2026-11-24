"""HTTP API untuk n8n. Bind 127.0.0.1 saja (§4.5, §8.6); bind-nya diatur di main.py."""

import secrets
from collections.abc import Callable
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Config
from .notifier import Outgoing
from .store import Store

ALERT_EVERY_S = 6 * 3600

Short = Field(min_length=1, max_length=200)


class JobIn(BaseModel):
    source: str = Short
    external_id: str = Short
    kind: Literal["item"]  # Fase 1: n8n cuma kirim item D; kind lain menyusul per fase
    payload: dict
    priority: Literal["normal", "urgent"] = "normal"


class CursorIn(BaseModel):
    last_checked: str = Short
    meta: dict | None = None


class AlertIn(BaseModel):
    key: str = Short
    message: str = Field(min_length=1, max_length=1000)


def create_app(config: Config, store: Store, notifier, wake: Callable[[], None]) -> FastAPI:
    """notifier: punya `async send(Outgoing)`. wake: bangunkan WorkerLoop."""
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    allowed_hosts = {f"127.0.0.1:{config.gateway_port}", f"localhost:{config.gateway_port}"}
    token = config.gateway_token.encode()

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Host dicek dulu: mitigasi DNS rebinding (§4.5)
        if request.headers.get("host", "").lower() not in allowed_hosts:
            return JSONResponse({"error": "host ditolak"}, status_code=400)
        given = request.headers.get("x-gateway-token", "").encode()
        if not secrets.compare_digest(given, token):
            return JSONResponse({"error": "token salah"}, status_code=403)
        return await call_next(request)

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.post("/jobs", status_code=201)
    async def create_job(body: JobIn, response: Response):
        job_id, created = store.create_job(
            body.source, body.external_id, body.kind, body.payload, body.priority
        )
        if not created:
            response.status_code = 200
            return {"id": job_id, "duplicate": True}
        if body.kind != "item":  # item nunggu digest, tidak perlu bangunkan worker
            wake()
        return {"id": job_id}

    @app.get("/cursor/{source}")
    async def get_cursor(source: str):
        cur = store.get_cursor(source)
        if cur is None:
            return JSONResponse({"error": "cursor belum ada"}, status_code=404)
        return cur

    @app.put("/cursor/{source}", status_code=204)
    async def put_cursor(source: str, body: CursorIn):
        store.put_cursor(source, body.last_checked, body.meta)
        return Response(status_code=204)

    @app.post("/alerts")
    async def alert(body: AlertIn):
        if not store.should_send_alert(body.key, ALERT_EVERY_S):
            return {"sent": False}
        await notifier.send(Outgoing(text=f"⚠️ {body.message}"))
        return {"sent": True}

    return app
