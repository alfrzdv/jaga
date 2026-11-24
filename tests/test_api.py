import httpx
import pytest

from gateway.api import create_app
from gateway.store import Status, Store

TOKEN = "t" * 40


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, msg):
        self.sent.append(msg)


@pytest.fixture
async def ctx(make_config):
    cfg = make_config(gateway_token=TOKEN, gateway_port=8787)
    store = Store(":memory:")
    notifier = FakeNotifier()
    woke = []
    app = create_app(cfg, store, notifier, wake=lambda: woke.append(1))
    # ASGITransport: app jalan di loop + thread yang sama, sama seperti uvicorn di main.py
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:8787") as client:
        client.headers["X-Gateway-Token"] = TOKEN
        yield client, store, notifier, woke
    store.close()


def item(ext="i1", **kw):
    return {"source": "web:lowongan", "external_id": ext, "kind": "item",
            "payload": {"title": "Magang"}} | kw  # fmt: skip


# --- keamanan -------------------------------------------------------------


@pytest.mark.parametrize("token", [None, "", "salah", TOKEN + "x"])
async def test_bad_token_rejected(ctx, token):
    client, store, *_ = ctx
    headers = {} if token is None else {"X-Gateway-Token": token}
    client.headers.pop("X-Gateway-Token")
    r = await client.post("/jobs", json=item(), headers=headers)
    assert r.status_code == 403
    assert store.list_jobs() == []


@pytest.mark.parametrize(
    "host", ["evil.com", "evil.com:8787", "127.0.0.1:9999", "192.168.0.5:8787"]
)
async def test_foreign_host_rejected(ctx, host):
    client, store, *_ = ctx
    r = await client.post("/jobs", json=item(), headers={"Host": host})
    assert r.status_code == 400
    assert store.list_jobs() == []


@pytest.mark.parametrize("host", ["127.0.0.1:8787", "localhost:8787"])
async def test_local_hosts_allowed(ctx, host):
    client, *_ = ctx
    assert (await client.get("/health", headers={"Host": host})).status_code == 200


async def test_docs_disabled(ctx):
    client, *_ = ctx
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 404


# --- jobs -----------------------------------------------------------------


async def test_create_job_then_duplicate(ctx):
    client, store, _, woke = ctx
    r1 = await client.post("/jobs", json=item())
    assert r1.status_code == 201
    job_id = r1.json()["id"]
    r2 = await client.post("/jobs", json=item(payload={"title": "beda"}))
    assert r2.status_code == 200
    assert r2.json() == {"id": job_id, "duplicate": True}
    job = store.get_job(job_id)
    assert job.status is Status.BARU and job.payload == {"title": "Magang"}
    assert woke == []  # item tidak membangunkan worker; diproses saat digest


@pytest.mark.parametrize(
    "body",
    [
        item(kind="chat"),  # Fase 1: n8n cuma boleh kirim item
        item(kind="send_email"),
        item(priority="darurat"),
        item(source=""),
        item(external_id=""),
        {"source": "web:x"},
    ],
)
async def test_invalid_job_rejected(ctx, body):
    client, store, *_ = ctx
    assert (await client.post("/jobs", json=body)).status_code == 422
    assert store.list_jobs() == []


# --- cursor ---------------------------------------------------------------


async def test_cursor_roundtrip(ctx):
    client, *_ = ctx
    assert (await client.get("/cursor/web:lowongan")).status_code == 404
    r = await client.put("/cursor/web:lowongan", json={"last_checked": "2026-11-24T00:00:00Z"})
    assert r.status_code == 204
    r = await client.get("/cursor/web:lowongan")
    assert r.status_code == 200
    assert r.json() == {"last_checked": "2026-11-24T00:00:00Z", "meta": None}


# --- alerts ---------------------------------------------------------------


async def test_alert_deduped(ctx):
    client, _, notifier, _ = ctx
    body = {"key": "rss-down", "message": "RSS lowongan error 503"}
    assert (await client.post("/alerts", json=body)).json() == {"sent": True}
    assert (await client.post("/alerts", json=body)).json() == {"sent": False}
    assert len(notifier.sent) == 1
    assert "RSS lowongan error 503" in notifier.sent[0].text
