#!/usr/bin/env bash
set -euo pipefail

if [[ -d "/opt/render/project/src" ]]; then
  cd /opt/render/project/src
else
  cd "$(dirname "$0")/.."
fi

if [[ -f ".venv/bin/activate" ]]; then
  source .venv/bin/activate
fi

mkdir -p "${LOG_DIR:-logs}"

exec python src/service.py
