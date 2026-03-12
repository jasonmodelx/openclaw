#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

LOG_DIR="/root/.openclaw/workspace/notebooklm-library/notebooklm/youtube/logs"
mkdir -p "$LOG_DIR"

TS_UTC="$(date -u +%F_%H%M%S)"
LOG_FILE="$LOG_DIR/${TS_UTC}.log"

{
  echo "[run] $(date -u +'%F %T UTC')"
  echo "[cwd] $(pwd)"
  echo "[log] $LOG_FILE"
  echo

  echo "=== keyword_search ==="
  PYTHONUNBUFFERED=1 python3 keyword_search.py || true

  echo
  echo "=== fetch_podcasts ==="
  PYTHONUNBUFFERED=1 python3 fetch_podcasts.py || true

  echo
  echo "[exit] $?"
} 2>&1 | tee -a "$LOG_FILE"
