"""Wrapper `claude -p` + validasi output + antrean Worker (concurrency 1). Lihat §5, §6.

Perilaku CLI yang jadi dasar kode ini ada di docs/findings.md (Fase 0 dan Fase 1).
"""

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from .config import REPO_ROOT, Config
from .store import Job, Status, Store

log = logging.getLogger(__name__)

WORKER_HOME_SRC = REPO_ROOT / "worker_home"

# Per kind: model, tools (--tools), max_turns, max_budget_usd. §5.2-5.3.
# max_turns tidak menghitung turn structured output (findings Fase 0).
KINDS: dict[str, dict] = {
    "chat": {"model": "haiku", "tools": "", "max_turns": 3, "max_budget_usd": 0.10},
    "digest": {"model": "haiku", "tools": "", "max_turns": 3, "max_budget_usd": 0.20},
}

TASKS = {
    "chat": "Jawab pesan owner di bawah. Pakai blok data di bawah kalau relevan.",
    "digest": (
        "Bikin digest dari item-item di blok data. Satu poin per item: judul, sumber, info penting"
        " (lokasi, deadline, gaji kalau ada), dan link. Urutkan dari yang paling relevan."
    ),
}

MAX_ATTEMPTS = 3  # 1 percobaan + 2 retry (§4.4)
STOP_GRACE_S = 10
AUTH_ALERT_EVERY_S = 6 * 3600
ITEM_MAX_CHARS = 1500

OnDone = Callable[[Job, "WorkerResult"], Awaitable[None]]
Notify = Callable[[str], Awaitable[None]]
ContextFn = Callable[[Store, str], str]


@dataclass
class WorkerResult:
    ok: bool
    output: dict | None  # structured_output
    error: str | None  # alasan gagal singkat, tanpa isi data
    model: str
    num_turns: int | None = None
    duration_ms: int | None = None
    total_cost_usd: float | None = None
    permission_denials: list = field(default_factory=list)
    auth_error: bool = False  # token Claude kosong/invalid/kedaluwarsa


def _is_auth_error(d: dict) -> bool:
    """Dari findings: token kosong → 'Not logged in', token salah → api_error_status 401."""
    text = str(d.get("result") or "")
    return (
        d.get("api_error_status") == 401
        or "Not logged in" in text
        or ("authenticate" in text.lower())
    )


def install_worker_home(config: Config) -> None:
    """Copy template worker_home/ ke JAGA_HOME/worker (ditimpa tiap start) + siapkan config dir.

    Gagal keras kalau ada CLAUDE.md di cwd Worker atau parent-nya (§5.1, lapisan tambahan).
    """
    shutil.copytree(WORKER_HOME_SRC, config.worker_dir, dirs_exist_ok=True)
    config.claude_config_dir.mkdir(parents=True, exist_ok=True)
    for d in (config.worker_dir, *config.worker_dir.parents):
        for name in ("CLAUDE.md", "CLAUDE.local.md"):
            if (d / name).exists():
                raise RuntimeError(f"{d / name} ada; Worker tidak boleh jalan di bawahnya")


def _exe(config: Config) -> list[str]:
    if config.claude_bin.endswith(".py"):  # tests/fake_claude.py
        return [sys.executable, config.claude_bin]
    return [config.claude_bin]


def build_command(config: Config, kind: str) -> list[str]:
    """argv sesuai template §5.2. Prompt TIDAK di argv; dikirim lewat stdin."""
    spec = KINDS[kind]
    schema = (config.worker_dir / "schemas" / f"{kind}.json").read_text(encoding="utf-8")
    return [
        *_exe(config),
        "-p",
        "--output-format", "json",
        "--json-schema", json.dumps(json.loads(schema), separators=(",", ":")),
        "--model", spec["model"],
        "--permission-mode", "dontAsk",
        "--permission-prompts", "none",
        "--tools", spec["tools"],
        "--max-turns", str(spec["max_turns"]),
        "--max-budget-usd", str(spec["max_budget_usd"]),
        "--no-session-persistence",
        "--setting-sources", "",
        "--safe-mode",
        "--strict-mcp-config",
        "--append-system-prompt-file", str(config.worker_dir / "system_prompt.md"),
    ]  # fmt: skip


def build_env(config: Config, base: dict[str, str]) -> dict[str, str]:
    env = {k: v for k, v in base.items() if k != "ANTHROPIC_API_KEY"}
    env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    env["CLAUDE_CONFIG_DIR"] = str(config.claude_config_dir)
    env["CLAUDE_CODE_OAUTH_TOKEN"] = config.claude_oauth_token
    return env


def parse_output(stdout: bytes, model: str) -> WorkerResult:
    """Sukses = JSON valid, is_error false, subtype success, structured_output object.

    Error API keluar sebagai subtype 'success' + is_error true, jadi is_error dicek duluan.
    """
    if not stdout.strip():
        return WorkerResult(False, None, "stdout kosong", model)
    try:
        d = json.loads(stdout)
    except ValueError:
        return WorkerResult(False, None, "stdout bukan JSON", model)
    if not isinstance(d, dict):
        return WorkerResult(False, None, "stdout bukan JSON object", model)

    res = WorkerResult(
        ok=False,
        output=None,
        error=None,
        model=model,
        num_turns=d.get("num_turns"),
        duration_ms=d.get("duration_ms"),
        total_cost_usd=d.get("total_cost_usd"),
        permission_denials=d.get("permission_denials") or [],
    )
    subtype, reason = d.get("subtype"), d.get("terminal_reason")
    if d.get("is_error") or subtype != "success":
        res.error = f"subtype={subtype} terminal_reason={reason}"
        if d.get("api_error_status"):
            res.error += f" api_error_status={d['api_error_status']}"
        res.auth_error = _is_auth_error(d)
        if res.auth_error:
            res.error = "auth Claude gagal; " + res.error
        return res
    so = d.get("structured_output")
    if not isinstance(so, dict):
        res.error = "structured_output hilang"
        return res
    res.ok, res.output = True, so
    return res


async def _stop(proc: asyncio.subprocess.Process) -> None:
    """Windows: CTRL_BREAK ke process group. POSIX: SIGINT. Lalu kill kalau masih hidup."""
    try:
        proc.send_signal(signal.CTRL_BREAK_EVENT if sys.platform == "win32" else signal.SIGINT)
    except OSError, ProcessLookupError, ValueError:
        pass
    try:
        await asyncio.wait_for(proc.wait(), STOP_GRACE_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()


async def run_claude(config: Config, kind: str, prompt: str) -> WorkerResult:
    model = KINDS[kind]["model"]
    extra = {}
    if sys.platform == "win32":
        extra["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        proc = await asyncio.create_subprocess_exec(
            *build_command(config, kind),
            cwd=config.worker_dir,
            env=build_env(config, dict(os.environ)),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **extra,
        )
    except OSError as e:
        reason = f"claude tidak bisa dijalankan ({type(e).__name__})"
        return WorkerResult(False, None, reason, model)

    try:
        out, err = await asyncio.wait_for(
            proc.communicate(prompt.encode("utf-8")), config.worker_timeout_s
        )
    except TimeoutError:
        await _stop(proc)
        return WorkerResult(False, None, "timeout", model)

    res = parse_output(out, model)
    if not res.ok:
        # stderr CLI berisi pesan error CLI, bukan data; tetap dipotong
        log.warning("worker %s gagal: %s (exit %s) %s", kind, res.error, proc.returncode, err[:200])
    if res.permission_denials:
        log.warning("worker %s: %d permission denial", kind, len(res.permission_denials))
    return res


_DATA_TAG = re.compile(r"<\s*/?\s*data\s*>", re.IGNORECASE)


def build_prompt(kind: str, instruction: str, data: str | None = None) -> str:
    """Tugas + pesan owner di luar <data>; semua isi sumber luar di dalam <data> (§5.6)."""
    parts = [TASKS[kind]]
    if instruction:
        parts.append(f"Pesan owner:\n{instruction}")
    if data:
        safe = _DATA_TAG.sub(lambda m: m.group().replace("<", "&lt;").replace(">", "&gt;"), data)
        parts.append(f"<data>\n{safe}\n</data>")
    return "\n\n".join(parts)


def _item_text(job: Job) -> str:
    text = json.dumps(job.payload, ensure_ascii=False)
    if len(text) > ITEM_MAX_CHARS:
        text = text[:ITEM_MAX_CHARS] + "…"
    return f"[{job.source}] {text}"


class WorkerLoop:
    def __init__(
        self,
        config: Config,
        store: Store,
        on_done: OnDone,
        notify: Notify,
        context_fn: ContextFn | None = None,
        backoff_s: float = 10,
        idle_s: float = 30,
    ):
        """on_done(job, result): setelah job selesai/gagal final. notify(text): pesan sistem."""
        self.config = config
        self.store = store
        self.on_done = on_done
        self.notify = notify
        self.context_fn = context_fn or (lambda store, text: "")
        self.backoff_s = backoff_s
        self.idle_s = idle_s
        self._wake = asyncio.Event()

    def wake(self) -> None:
        self._wake.set()

    async def _allowed_kinds(self) -> list[str]:
        allowed = []
        for kind in KINDS:
            limit = self.config.daily_limits.get(kind)
            if limit is None or self.store.calls_today(kind) < limit:
                allowed.append(kind)
                continue
            waiting = self.store.count_waiting(kind)
            key = f"limit:{kind}:{datetime.now().date().isoformat()}"
            if waiting and self.store.should_send_alert(key, 86_400):
                await self.notify(
                    f"Batas harian Worker untuk '{kind}' ({limit}x) kepake. "
                    f"{waiting} job nunggu sampai besok."
                )
        return allowed

    def _prompt_for(self, job: Job) -> tuple[str, list[int]]:
        payload = job.payload or {}
        if job.kind == "chat":
            text = payload.get("text", "")
            return build_prompt("chat", text, self.context_fn(self.store, text)), []
        if job.kind == "digest":
            items = [self.store.get_job(i) for i in payload.get("item_ids", [])]
            items = [i for i in items if i and i.status is Status.BARU and i.payload]
            data = "\n".join(_item_text(i) for i in items)
            return build_prompt("digest", "", data), [i.id for i in items]
        raise KeyError(job.kind)

    async def _finish(self, job: Job, result: WorkerResult) -> None:
        try:
            await self.on_done(self.store.get_job(job.id), result)
        except Exception:
            log.exception("on_done gagal untuk job %s", job.id)

    async def run_once(self) -> bool:
        """Proses satu job. Return False kalau tidak ada yang bisa diproses."""
        job = self.store.claim_next(await self._allowed_kinds())
        if job is None:
            return False
        try:
            await self._process(job)
        except Exception:
            log.exception("job %s error internal", job.id)
            to = Status.BARU if job.attempts < MAX_ATTEMPTS else Status.GAGAL
            self.store.transition(job.id, to, result="error internal Gateway")
        return True

    async def _process(self, job: Job) -> None:
        prompt, item_ids = self._prompt_for(job)
        if job.kind == "digest" and not item_ids:
            self.store.transition(job.id, Status.SELESAI, result="tidak ada item")
            return

        result = await run_claude(self.config, job.kind, prompt)
        if result.auth_error:
            # bukan salah job: jangan bakar percobaan, jangan hitung ke batas harian
            self.store.release(job.id, result=result.error)
            if self.store.should_send_alert("claude-auth", AUTH_ALERT_EVERY_S):
                await self.notify(
                    "Token Claude Worker invalid atau kedaluwarsa. Jalankan `claude setup-token`"
                    " di laptop, isi CLAUDE_CODE_OAUTH_TOKEN di .env, lalu restart Gateway."
                    " Job tetap antre."
                )
            await asyncio.sleep(self.backoff_s * 30)
            return
        self.store.log_usage(
            job.id, result.model, result.num_turns, result.duration_ms, result.total_cost_usd
        )

        if result.ok:
            if result.output.get("aksi_diajukan"):
                # Fase 1 belum ada executor; aksi tidak pernah dieksekusi atau diajukan ke owner
                log.warning("job %s: aksi_diajukan diabaikan (Fase 1)", job.id)
            with self.store.transaction():
                if item_ids:
                    self.store.finish_items(item_ids, result=f"digest:{job.id}")
                self.store.transition(job.id, Status.SELESAI, proposal=result.output)
                if job.kind == "chat":
                    self.store.add_chat("assistant", result.output.get("ringkasan", ""), job.id)
            await self._finish(job, result)
        elif job.attempts < MAX_ATTEMPTS:
            self.store.transition(job.id, Status.BARU, result=result.error)
            await asyncio.sleep(self.backoff_s * job.attempts)
        else:
            self.store.transition(job.id, Status.GAGAL, result=result.error)
            await self._finish(job, result)

    async def run(self) -> None:
        while True:
            self._wake.clear()
            try:
                processed = await self.run_once()
            except Exception:
                log.exception("worker loop error")
                processed = False
            if not processed:
                try:
                    await asyncio.wait_for(self._wake.wait(), self.idle_s)
                except TimeoutError:
                    pass
