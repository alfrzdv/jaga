"""Pengganti `claude` untuk tes. Dipilih lewat CLAUDE_BIN di .env tes.

Mode dari env FAKE_CLAUDE_MODE:
  success           → tests/samples/success.json (exit 0)
  error_max_turns   → tests/samples/error_max_turns.json (exit 1)
  permission_denied → tests/samples/permission_denied.json (exit 0)
  no_structured     → success.json tanpa key structured_output (exit 0)
  empty             → stdout kosong (meniru timeout di Windows)
  hang              → tidur lama (untuk tes timeout)
  garbage           → stdout bukan JSON

TODO:
- FAKE_CLAUDE_ARGV_LOG=<path>: tulis argv + env penting (tanpa token) ke file, supaya tes
  bisa memastikan flag isolasi (§5.1) selalu ada
- FAKE_CLAUDE_OUTPUT=<json>: override structured_output
"""

if __name__ == "__main__":
    raise NotImplementedError
