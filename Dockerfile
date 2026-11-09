# Self-contained image: builds the interface, then serves it and the API from one process.
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
RUN pip install --no-cache-dir uv==0.13.7
WORKDIR /srv/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/app ./app
COPY --from=web /web/dist /srv/frontend/dist
RUN useradd --create-home tutor && mkdir -p /srv/data && chown tutor /srv/data
USER tutor
ENV DATABASE_URL=sqlite:////srv/data/tutor.db
EXPOSE 8000
CMD ["sh", "-c", "exec .venv/bin/uvicorn --factory app.main:create_app --host 0.0.0.0 --port ${PORT:-8000}"]
