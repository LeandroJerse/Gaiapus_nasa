#!/usr/bin/env bash
# Daily Gaiapus snapshot (Linux/macOS). Example cron (06:30 UTC):
#   30 6 * * * cd /opt/gaiapus && ./run_daily.sh
# Needs EARTHDATA_TOKEN exported (e.g. in /etc/environment or a .env loaded by your service manager).
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$PWD"
mkdir -p logs
"${GAIAPUS_PYTHON:-python3}" -u -m data.serving.daily_job --mode live --out dist/snapshots_live --workers 6 >> "logs/daily_$(date -u +%F).log" 2>&1
