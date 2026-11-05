#!/usr/bin/env sh
# Consistent backup of the bot's SQLite database while the bot keeps running.
#
#   deploy/backup.sh                 # → data/backups/olx_hunter-YYYY-MM-DD_HHMM.db
#   KEEP_DAYS=30 deploy/backup.sh    # delete backups older than 30 days (default 14)
#
# Uses SQLite's online backup API inside the bot container (WAL-safe; copying the
# .db file alone could miss recent writes). Cron example (daily 04:30):
#   30 4 * * * cd /path/to/olx-hunter-bot && deploy/backup.sh >> data/backups/backup.log 2>&1
set -eu

cd "$(dirname "$0")/.."
KEEP_DAYS="${KEEP_DAYS:-14}"
name="olx_hunter-$(date +%Y-%m-%d_%H%M).db"

mkdir -p data/backups
docker compose exec -T bot python -c "
import sqlite3, sys
src = sqlite3.connect('/data/olx_hunter.db')
dst = sqlite3.connect('/data/backups/' + sys.argv[1])
with dst:
    src.backup(dst)
dst.close(); src.close()
" "$name"

find data/backups -name 'olx_hunter-*.db' -mtime +"$KEEP_DAYS" -delete
echo "$(date -Is) backup ok: data/backups/$name"
