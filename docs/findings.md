# Findings

Hasil verifikasi selama development. Tiap entri: tanggal, apa yang dites, hasilnya.

## Fase 0 — Verifikasi mesin (2026-11-24)

### Versi

| Komponen | Versi | Target | Status |
|---|---|---|---|
| OS | Windows 11 Home 10.0.26300 | - | - |
| Python (`C:\Python314\python.exe`, CPython resmi) | 3.14.x, SQLite **3.50.4** | 3.11+, SQLite ≥ 3.51.3 | Python ok, SQLite di bawah batas |
| Python (`/c/msys64/mingw64/bin/python`, default di PATH Git Bash) | 3.14.6, SQLite 3.53.4 | - | build MINGW, jangan dipakai (lihat bawah) |
| Python 3.11 (`AppData\Local\Programs\Python\Python311`) | 3.11, SQLite 3.45.1 | - | cadangan |
| Node | 24.11.1 | 24.x | ok |
| npm | 11.6.2 | - | ok |
| Claude Code | 2.1.323 | ≥ 2.1.302 | ok |
| n8n | belum terinstall | 2.x | npm `stable` = 2.48.8, `engines.node >= 24.0.0` |

`python` di PATH Git Bash adalah build MSYS2/MINGW. Banyak paket dengan ekstensi C (pydantic-core, dll.) nggak punya wheel untuk MINGW, jadi venv Gateway sebaiknya dibuat dari `C:\Python314\python.exe`. Konsekuensinya SQLite 3.50.4 kena warning di §4.3.

### `claude -p`: flag

Dicek dari `claude --help` (2.1.323) dan dites langsung.

- `--append-system-prompt-file <path>` **jalan** walaupun nggak muncul di `--help` (cuma disebut di deskripsi `--bare`). Dibuktikan pakai canary di file prompt.
- `--tools ""` mematikan semua tool bawaan. Ini lebih tepat dari `--allowedTools` untuk kind tanpa tools: `--allowedTools` cuma meng-*approve*, `--tools` membatasi tool yang *tersedia*. Structured output tetap jalan (`tool_schema_build_detail.tools = 1`, yaitu tool structured output).
- `--permission-prompts none` ada, choices `host`/`none`.
- `--permission-mode` choices: `acceptEdits, auto, bypassPermissions, manual, dontAsk, plan`.
- Flag baru yang relevan: `--safe-mode` dan `--restricted` (lihat bagian isolasi).
- Tanpa stdin, CLI menunggu 3 detik lalu print warning `no stdin data received in 3s`. Subprocess wajib `stdin=subprocess.DEVNULL`.

### `claude -p`: isolasi (§5.1)

Metode: cwd `~/.jaga/worker/`, canary `CANARY-APPEND-7731` di file `--append-system-prompt-file`, canary `PARENT-CANARY-5519` ditanam sementara di `~/.jaga/CLAUDE.md` (parent dari cwd), dan `~/.claude/CLAUDE.md` asli (isinya soal graphify) dipakai sebagai canary user-level. Selain self-report model, dicek juga lewat `--debug-file`.

| Mode | CLAUDE.md user (graphify) | CLAUDE.md parent | Skill dari `~/.claude/skills` | Allow rules `~/.claude/settings.json` | Auth langganan |
|---|---|---|---|---|---|
| `--setting-sources user` | **kebaca** | nggak | 50 | kebaca | ok |
| `--safe-mode` | nggak | nggak | 0 | masih kebaca | ok |
| `--setting-sources ""` | nggak | nggak | 0 | nggak | ok |
| `--setting-sources "" --safe-mode` | nggak | nggak | 0 | nggak | ok |

Kesimpulan:
- `--setting-sources user` di template §5.2 **bocor**: `~/.claude/CLAUDE.md` ikut kebaca. Jangan dipakai.
- Tanpa `project` di setting sources, CLAUDE.md di cwd/parent nggak dimuat. Ini konsisten di semua mode.
- Dipakai: `--setting-sources "" --safe-mode`. Auth langganan tetap jalan (`ANTHROPIC_API_KEY` di-unset, run sukses).
- `--bare` nggak dites karena dari help-nya jelas cuma terima `ANTHROPIC_API_KEY`/`apiKeyHelper`.
- Yang masih bocor: harness tetap menyuntik email akun Claude ke konteks (system-reminder). Isinya bukan instruksi, jadi bukan masalah isolasi, tapi berarti Worker tahu email akun. Kalau mau dihilangkan, opsi `CLAUDE_CONFIG_DIR` terpisah + `claude setup-token` (§5.1 opsional) perlu dites.
- Built-in plugin (`cc-plugin-agents-md`, `cc-plugin-telemetry`, `cc-plugin-privacy`, `cc-plugin-plugin-authoring`) tetap load di semua mode. Itu bawaan binary.

### `claude -p`: output JSON

Sampel asli di `~/.jaga/logs/p7_final.json` (di luar repo). Key top-level yang dipakai Gateway:

```
type            "result"
subtype         "success"
is_error        false
num_turns       2
duration_ms     2484
total_cost_usd  0.0009651
permission_denials  []
terminal_reason "completed"
result          string JSON (sama dengan structured_output, diserialisasi)
structured_output   object sesuai --json-schema
session_id, usage, modelUsage, api_error_status, credential_expires_at, ...
```

- `structured_output` ada di top-level, sesuai §4.6.
- `credential_expires_at` ikut keluar. Bisa dipakai buat alert "token langganan mau habis".
- `total_cost_usd` tetap terisi walau pakai langganan. Angka list price, bukan tagihan.
- Model `haiku` resolve ke `claude-haiku-5-5`.
- Satu panggilan tanpa tools + structured output = `num_turns: 2`. `--max-turns 1` **tetap sukses** dengan `num_turns: 2`, jadi turn structured output kayaknya nggak dihitung. Sampel `error_max_turns` asli belum didapat. Perlu kasus pakai tools (Fase 4) untuk memicunya.

### Timeout di Windows

Tes: `claude -p` tugas panjang, dihentikan setelah 6 detik (`subprocess.Popen` dengan `CREATE_NEW_PROCESS_GROUP`).

| Cara | Exit code | stdout |
|---|---|---|
| `send_signal(CTRL_BREAK_EVENT)` | 0 | kosong |
| `terminate()` | 1 | kosong |

Windows nggak punya SIGINT untuk subprocess, dan dua-duanya nggak menghasilkan JSON result. Artinya di Windows, timeout = gagal tanpa data usage. `worker.py` harus menganggap stdout kosong sebagai gagal dan mencatat usage sebagai tidak diketahui. Exit 0 dari CTRL_BREAK juga bukan tanda sukses, jadi yang dicek isi stdout, bukan exit code.

### Template pemanggilan yang terverifikasi

```bash
cd ~/.jaga/worker
env -u ANTHROPIC_API_KEY CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 \
claude -p "<prompt>" \
  --output-format json \
  --json-schema "<isi schemas/<kind>.json>" \
  --model haiku \
  --permission-mode dontAsk \
  --permission-prompts none \
  --tools "" \
  --max-turns <N> \
  --max-budget-usd <X> \
  --no-session-persistence \
  --setting-sources "" \
  --safe-mode \
  --strict-mcp-config \
  --append-system-prompt-file system_prompt.md \
  < /dev/null
```

Untuk `file_task`: ganti `--tools ""` dengan `--tools "Read,Glob,Grep"` (belum dites, Fase 4).

### Belum dikerjakan di Fase 0

- [ ] Install n8n 2.x, set `N8N_LISTEN_ADDRESS=127.0.0.1`, tes HTTP Request ke 127.0.0.1.
