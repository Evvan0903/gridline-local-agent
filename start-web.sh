#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Missing .venv. Create the environment and install requirements.txt first.' >&2; exit 1
fi
if ! command -v npm >/dev/null 2>&1; then echo 'Node.js and npm are required.' >&2; exit 1; fi
if [[ ! -d web/node_modules ]]; then echo 'Install frontend dependencies first: cd web && npm ci' >&2; exit 1; fi
.venv/bin/python -B - <<'PY'
import socket, sys
for port in (3000, 3001):
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try: listener.bind(('127.0.0.1',port))
        except OSError:
            print(f'Port {port} is already in use. Stop that service or choose another session; no process was killed.',file=sys.stderr)
            sys.exit(1)
PY
api_pid=''; web_pid=''
cleanup() {
  trap - EXIT INT TERM
  [[ -z "$web_pid" ]] || kill -TERM "$web_pid" 2>/dev/null || true
  [[ -z "$api_pid" ]] || kill -TERM "$api_pid" 2>/dev/null || true
  [[ -z "$web_pid" ]] || wait "$web_pid" 2>/dev/null || true
  [[ -z "$api_pid" ]] || wait "$api_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM
.venv/bin/python -B -m web_api & api_pid=$!
# exec the Vite process directly so shutdown targets the owned server, not an npm wrapper.
(cd web && exec node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 3000 --strictPort) & web_pid=$!
.venv/bin/python -B - <<'PYREADY'
import sys, time
from urllib.request import urlopen
for attempt in range(60):
    try:
        with urlopen('http://127.0.0.1:3001/api/health', timeout=.5) as response:
            if response.status == 200:
                break
    except OSError:
        time.sleep(.1)
else:
    print('The backend did not become ready; stopping this session.', file=sys.stderr)
    sys.exit(1)
PYREADY
echo 'Gridline local workspace: http://localhost:3000'
echo 'Both services bind to 127.0.0.1. Ctrl-C stops this session.'
while kill -0 "$api_pid" 2>/dev/null && kill -0 "$web_pid" 2>/dev/null; do sleep 1; done
echo 'A local service stopped; shutting down this session.' >&2
exit 1
