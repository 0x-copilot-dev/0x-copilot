#!/usr/bin/env bash
# The account-merge LIVE-Postgres gate (docs/plan/account-linking/PRD.md §8),
# as one repeatable command: spins a disposable UTF-8 Postgres cluster, runs
# both services' live merge suites (real schema, real RLS scripts, real
# envelope-AAD ciphertext) plus the backend RLS isolation test, and tears the
# cluster down. Requires local Postgres binaries (initdb/pg_ctl/psql — e.g.
# `brew install postgresql`).
#
#   make test-merge-live      # or: bash tools/run-merge-live-gate.sh
#
# Notes:
# - The cluster MUST be UTF-8 (ENCODING 'UTF8'); a C-locale/SQL_ASCII cluster
#   breaks yoyo on the migrations' non-ASCII comments. PYTHONUTF8=1 likewise.
# - A non-superuser role is created so RLS assertions are real (superusers
#   bypass RLS by design).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# Worktree-friendly: venvs live in the primary checkout; override when
# running from a git worktree (e.g. BACKEND_PY=/path/to/main/services/backend/.venv/bin/python).
BACKEND_PY="${BACKEND_PY:-$ROOT/services/backend/.venv/bin/python}"
AI_PY="${AI_PY:-$ROOT/services/ai-backend/.venv/bin/python}"
SHARED_PATH="../../packages/service-contracts/src:../../packages/audit-chain/src"
PORT="${MERGE_GATE_PG_PORT:-55499}"
WORKDIR="$(mktemp -d /tmp/merge-gate.XXXXXX)"
DATA="$WORKDIR/data"
LOG="$WORKDIR/pg.log"
PSQL=(psql -h 127.0.0.1 -p "$PORT" -U postgres)

cleanup() {
  pg_ctl -D "$DATA" stop -m immediate >/dev/null 2>&1 || true
  rm -rf "$WORKDIR"
}
trap cleanup EXIT

echo "==> initdb (UTF-8) + start on :$PORT"
initdb -D "$DATA" -U postgres -A trust -E UTF8 >/dev/null
pg_ctl -D "$DATA" -l "$LOG" -o "-p $PORT -c unix_socket_directories=''" start >/dev/null
for _ in $(seq 1 20); do
  "${PSQL[@]}" -c "SELECT 1" >/dev/null 2>&1 && break
  sleep 0.5
done

echo "==> create databases + non-superuser app role"
"${PSQL[@]}" -c "CREATE DATABASE merge_backend_gate ENCODING 'UTF8' TEMPLATE template0" >/dev/null
"${PSQL[@]}" -c "CREATE DATABASE merge_runtime_gate ENCODING 'UTF8' TEMPLATE template0" >/dev/null
"${PSQL[@]}" -d merge_backend_gate >/dev/null <<'SQL'
CREATE ROLE merge_gate_app LOGIN;
GRANT CONNECT ON DATABASE merge_backend_gate TO merge_gate_app;
GRANT USAGE ON SCHEMA public TO merge_gate_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO merge_gate_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO merge_gate_app;
SQL

BACKEND_URL="postgresql://postgres@127.0.0.1:$PORT/merge_backend_gate"
BACKEND_APP_URL="postgresql://merge_gate_app@127.0.0.1:$PORT/merge_backend_gate"
RUNTIME_URL="postgresql://postgres@127.0.0.1:$PORT/merge_runtime_gate"

echo "==> backend: live merge saga + RLS isolation"
(
  cd "$ROOT/services/backend"
  PYTHONUTF8=1 \
  PYTHONPATH="src:$SHARED_PATH" \
  BACKEND_MERGE_TEST_DATABASE_URL="$BACKEND_URL" \
  BACKEND_MERGE_TEST_APP_DATABASE_URL="$BACKEND_APP_URL" \
  BACKEND_RLS_TEST_DATABASE_URL="$BACKEND_URL" \
  BACKEND_RLS_TEST_APP_DATABASE_URL="$BACKEND_APP_URL" \
  "$BACKEND_PY" -m pytest \
    tests/integration/persistence/test_account_merge_live.py \
    tests/integration/persistence/test_rls_isolation.py \
    tests/integration/persistence/test_principals_live.py \
    tests/integration/persistence/test_principal_edges_live.py -q
)

echo "==> ai-backend: live re-key + envelope-AAD decrypt smoke"
(
  cd "$ROOT/services/ai-backend"
  PYTHONUTF8=1 \
  PYTHONPATH="src:$SHARED_PATH" \
  MERGE_LIVE_TEST_DATABASE_URL="$RUNTIME_URL" \
  "$AI_PY" -m pytest \
    tests/integration/persistence/test_account_merge_live.py -q
)

echo "==> merge live gate: PASS"
