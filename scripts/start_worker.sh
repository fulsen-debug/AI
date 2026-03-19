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

exec python src/bot.py
