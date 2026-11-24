"""Pengganti `claude` untuk tes. Dipakai lewat CLAUDE_BIN=tests/fake_claude.py.

Mode dari env FAKE_CLAUDE_MODE (default success):
  success           → samples/success.json (exit 0)
  error_max_turns   → samples/error_max_turns.json (exit 1)
  permission_denied → samples/permission_denied.json (exit 0)
  api_error         → samples/api_error.json (exit 1)
  auth_error        → samples/auth_error.json (exit 1, token invalid)
  no_structured     → success.json tanpa key structured_output (exit 0)
  empty             → stdout kosong (meniru timeout di Windows)
  hang              → tidur lama (untuk tes timeout)
  garbage           → stdout bukan JSON

Env lain:
  FAKE_CLAUDE_OUTPUT=<json>    override structured_output (dan result) di mode success
  FAKE_CLAUDE_ARGV_LOG=<path>  tulis {argv, cwd, stdin, env} ke file JSON; env cuma berisi
                               key yang relevan untuk isolasi, token dicatat sebagai 'set'
"""

import json
import os
import sys
import time
from pathlib import Path

SAMPLES = Path(__file__).parent / "samples"
ENV_KEYS = (
    "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_OAUTH_TOKEN",
)
SECRET_KEYS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


def sample(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text(encoding="utf-8"))


def main() -> int:
    stdin = sys.stdin.read()
    if log := os.environ.get("FAKE_CLAUDE_ARGV_LOG"):
        env = {}
        for k in ENV_KEYS:
            if k in os.environ:
                env[k] = "set" if k in SECRET_KEYS else os.environ[k]
        Path(log).write_text(
            json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "stdin": stdin, "env": env}),
            encoding="utf-8",
        )

    mode = os.environ.get("FAKE_CLAUDE_MODE", "success")
    if mode == "hang":
        time.sleep(600)
        return 0
    if mode == "empty":
        return 0
    if mode == "garbage":
        print("Error: sesuatu yang bukan JSON")
        return 1

    exit_code = 0
    if mode == "success":
        out = sample("success.json")
        if override := os.environ.get("FAKE_CLAUDE_OUTPUT"):
            out["structured_output"] = json.loads(override)
            out["result"] = override
    elif mode == "no_structured":
        out = sample("success.json")
        del out["structured_output"]
    elif mode == "permission_denied":
        out = sample("permission_denied.json")
    elif mode == "error_max_turns":
        out, exit_code = sample("error_max_turns.json"), 1
    elif mode == "api_error":
        out, exit_code = sample("api_error.json"), 1
    elif mode == "auth_error":
        out, exit_code = sample("auth_error.json"), 1
    else:
        print(f"mode tidak dikenal: {mode}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
