#!/usr/bin/env bash
# Avvia backend + replay in dev mode. Cleanup automatico su CTRL-C.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"

# Preferisci venv locale se esiste, altrimenti usa system python (richiede deps installate a livello user)
if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="$(command -v python3)"
  echo ">> Using system python ($PY). For isolated env: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
fi

# Genera mock se mancanti
if ! ls data/csv/tp_snapshot_*.csv >/dev/null 2>&1; then
  echo ">> Generating mock scenario..."
  "$PY" scripts/generate_mock_scenario.py
fi

# Avvia replay in background
echo ">> Starting replay loop..."
"$PY" scripts/replay_mocks.py &
REPLAY_PID=$!

cleanup() {
  echo
  echo ">> Cleanup: killing replay ($REPLAY_PID)"
  kill "$REPLAY_PID" 2>/dev/null || true
}
trap cleanup EXIT

echo ">> Starting FastAPI on http://localhost:${PORT:-8765}"
exec "$PY" -m uvicorn backend.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8765}" --no-access-log
