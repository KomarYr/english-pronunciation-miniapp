# syntax=docker/dockerfile:1
# ===== Stage 1: frontend ====
FROM node:24-slim AS frontend
WORKDIR /build

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
RUN npm run build

# ==== Stage 2: python + uv =====
FROM python:3.14-slim-trixie

#COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates ffmpeg libasound2t64 \
    && rm -rf /var/lib/apt/lists/*


COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=bind,from=ghcr.io/astral-sh/uv:0.13.6,source=/uv,target=/usr/local/bin/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src/ ./src/
RUN --mount=type=bind,from=ghcr.io/astral-sh/uv:0.13.6,source=/uv,target=/usr/local/bin/uv \
    uv sync --frozen --no-dev

COPY --from=frontend /build/dist ./frontend/dist

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid 10001 --no-create-home \
        --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown 10001:10001 /app/data

USER 10001:10001

ENV MINIAPP_HOST=0.0.0.0 \
    MINIAPP_PORT=8000 \
    APP_ROOT=/app \
    DATA_DIR=/app/data

EXPOSE 8000
CMD [ "azure-mini-app-web" ]
