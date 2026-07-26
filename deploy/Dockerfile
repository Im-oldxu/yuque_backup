FROM node:24.11.1-bookworm-slim AS frontend-builder

ENV COREPACK_HOME=/corepack
ENV PATH="${COREPACK_HOME}:${PATH}"

WORKDIR /src/frontend
RUN corepack enable && corepack prepare pnpm@10.29.1 --activate
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM python:3.13.12-slim-bookworm AS binary-builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN apt-get update \
    && apt-get install --no-install-recommends --yes binutils \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir uv==0.10.0

WORKDIR /src/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --group build --no-install-project

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/alembic.ini backend/yuque_backup.spec ./
COPY --from=frontend-builder /src/frontend/dist /src/frontend/dist
RUN uv sync --frozen --no-dev --group build \
    && uv run --frozen --no-sync pyinstaller --clean --noconfirm yuque_backup.spec

FROM debian:bookworm-slim AS runtime

ARG VERSION=dev
ARG REVISION=unknown
ARG SOURCE=https://github.com/Im-oldxu/yuque_backup

LABEL org.opencontainers.image.title="Yuque Backup" \
      org.opencontainers.image.description="Self-hosted read-only Yuque backup service" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="${SOURCE}"

ENV APP_ENV=production \
    DATA_ROOT=/data \
    DATABASE_URL=sqlite:////data/db/yuque-backup.sqlite3 \
    HOST=0.0.0.0 \
    PORT=8000 \
    TZ=Asia/Shanghai \
    SECURE_COOKIES=true

RUN apt-get update \
    && apt-get install --no-install-recommends --yes ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /data/db /data/content \
    && chown -R app:app /data

COPY --from=binary-builder /src/backend/dist/yuque-backup /usr/local/bin/yuque-backup

USER 10001:10001
WORKDIR /app
EXPOSE 8000

ENTRYPOINT ["yuque-backup"]
CMD ["api"]
