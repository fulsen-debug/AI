#!/usr/bin/env bash
set -euo pipefail

if [[ -d "/opt/render/project/src" ]]; then
  cd /opt/render/project/src
else
  cd "$(dirname "$0")/.."
fi

mkdir -p logs

if [[ -f ".venv/bin/activate" ]]; then
  # Local/dev convenience. Render does not require this.
  source .venv/bin/activate
fi

if [[ "${BOT_MODE:-paper}" == "live" ]] && [[ "${LIVE_TRADING_ACK:-}" != "I_UNDERSTAND_LIVE_RISK" ]]; then
  echo "Refusing to start live mode: set LIVE_TRADING_ACK=I_UNDERSTAND_LIVE_RISK"
  exit 1
fi

# Render web services always provide PORT. If this script is accidentally
# configured as Start Command on a web service, run the web dashboard so
# Render can detect an open HTTP port.
if [[ -n "${PORT:-}" ]] || [[ "${RUN_AS_WEB:-0}" == "1" ]]; then
  mkdir -p "${LOG_DIR:-logs}"
  exec python src/service.py
fi

exec python src/bot.py
