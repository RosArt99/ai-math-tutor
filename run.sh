#!/usr/bin/env bash
# Build the interface and start the app on http://localhost:8000
set -euo pipefail
cd "$(dirname "$0")"

command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/" >&2; exit 1; }
command -v npm >/dev/null || { echo "Node.js 20+ is required: https://nodejs.org/" >&2; exit 1; }
[ -f .env ] || { cp .env.example .env; echo "Created .env: add your OPENROUTER_API_KEY to it."; }

(cd frontend && npm ci --no-audit --no-fund && npm run build)
cd backend
uv sync --frozen --python 3.12
exec uv run --frozen uvicorn --factory app.main:create_app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
