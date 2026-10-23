#!/bin/sh
# Consistent backup of the Stove Pilot SQLite database (history and suspension state).
#
# Uses python's sqlite3 Connection.backup (the online backup API), which is safe while the API
# is running. Never copy the live .sqlite file with cp: with WAL active, recent writes sit in the
# -wal file and a plain copy can be torn or miss them.
#
# Usage:
#   stove-pilot-backup.sh docker [BACKUP_DIR]   # api container of the compose stack
#   stove-pilot-backup.sh local  [BACKUP_DIR]   # venv/systemd install, reads STOVE_PILOT_DB_PATH
#
# Environment (all optional):
#   STOVE_PILOT_BACKUP_KEEP   number of backups to keep (default 14)
#   STOVE_PILOT_DB_PATH       database path for local mode (default /var/lib/stove-pilot/stove-pilot.sqlite)
#   STOVE_PILOT_PYTHON        python used in local mode (default /opt/stove-pilot/venv/bin/python)
#   STOVE_PILOT_COMPOSE_DIR   directory holding compose.yaml (default: two levels above this script)
set -eu

MODE="${1:-}"
BACKUP_DIR="${2:-./backups}"
KEEP="${STOVE_PILOT_BACKUP_KEEP:-14}"

# Online backup of argv[1] into argv[2], then verify the copy.
PY='
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
result = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close()
src.close()
if result != "ok":
    sys.exit("integrity_check failed: " + result)
'

usage() {
    echo "usage: $0 docker|local [BACKUP_DIR]" >&2
    exit 2
}

case "$MODE" in
    docker | local) ;;
    *) usage ;;
esac

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y-%m-%d)"
FINAL="$BACKUP_DIR/stove-$STAMP.sqlite"
PARTIAL="$FINAL.partial"
trap 'rm -f "$PARTIAL"' EXIT

if [ "$MODE" = "docker" ]; then
    COMPOSE_DIR="${STOVE_PILOT_COMPOSE_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
    COMPOSE="docker compose -f $COMPOSE_DIR/compose.yaml"
    $COMPOSE exec -T api python -c "$PY" /data/stove-pilot.sqlite /tmp/stove-backup.sqlite
    $COMPOSE exec -T api cat /tmp/stove-backup.sqlite > "$PARTIAL"
    $COMPOSE exec -T api rm -f /tmp/stove-backup.sqlite
else
    DB_PATH="${STOVE_PILOT_DB_PATH:-/var/lib/stove-pilot/stove-pilot.sqlite}"
    PYTHON="${STOVE_PILOT_PYTHON:-/opt/stove-pilot/venv/bin/python}"
    "$PYTHON" -c "$PY" "$DB_PATH" "$PARTIAL"
fi

mv -f "$PARTIAL" "$FINAL"
echo "backup written: $FINAL"

# Retention: names sort by date, keep the newest $KEEP.
ls -1 "$BACKUP_DIR"/stove-*.sqlite 2>/dev/null | sort -r | tail -n +"$((KEEP + 1))" |
    while IFS= read -r old; do
        rm -f -- "$old"
        echo "removed old backup: $old"
    done
