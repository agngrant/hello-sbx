#!/bin/sh
# LittleDungeons convenience launcher — starts the stdlib-only server on 127.0.0.1:8000.
# Prefers the project venv (./.venv/bin/python) if present, else python3.
cd "$(dirname "$0")" || exit 1

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

exec "$PY" -m app.main --host 127.0.0.1 --port 8000
