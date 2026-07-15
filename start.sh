#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
elif [[ -f .venv/bin/python ]]; then
  PATH="$(pwd)/.venv/bin:$PATH"
fi

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec python app.py
