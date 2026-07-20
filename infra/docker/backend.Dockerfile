FROM ghcr.io/astral-sh/uv:0.11.29 AS uv

FROM python:3.12.13-slim-bookworm AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock .python-version ./
COPY packages ./packages
COPY services ./services

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-groups --no-editable

FROM python:3.12.13-slim-bookworm AS runtime

ENV PATH=/app/.venv/bin:${PATH} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app /data/cache /data/outputs /data/quality-reports \
    && chown -R app:app /app /data

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/pyproject.toml /app/pyproject.toml
COPY --from=builder --chown=app:app /app/packages /app/packages
COPY --from=builder --chown=app:app /app/services /app/services
COPY --chown=app:app docker-compose.yml /app/docker-compose.yml
COPY --chown=app:app apps/web/Dockerfile /app/apps/web/Dockerfile
COPY --chown=app:app alembic.ini /app/alembic.ini
COPY --chown=app:app docs/openapi.yaml /app/docs/openapi.yaml
COPY --chown=app:app data/manifest.json /app/data/manifest.json
COPY --chown=app:app data/boundaries /app/data/boundaries
COPY --chown=app:app infra/docker/backend.Dockerfile /app/infra/docker/backend.Dockerfile
COPY --chown=app:app infra/db/postgis/Dockerfile /app/infra/db/postgis/Dockerfile
COPY --chown=app:app infra/db/migrations /app/infra/db/migrations
COPY --chown=app:app scripts /app/scripts
COPY --chown=app:app tests /app/tests

USER 10001:10001

EXPOSE 8000 8001 8002 8003 8004
