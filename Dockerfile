# One Infrahub Sync image for the Sync API, the service worker, deployment
# bootstrap, and one-off CLI or Python commands.
#
# Every external base carries its digest. The tag beside a digest is there for a
# human reader; the digest is the only authority, so a re-pointed tag cannot
# change what a rebuild of a recorded revision produces.

FROM python:3.13-slim-bookworm@sha256:ed86c82274b3c69b52fb5820f358f0bd7df0b603332063cb5c6e32bd220c3e6e AS base

# ---------------------------------------------------------------------------
# Build stage: resolve nothing, install the committed lock, keep uv out of the
# runtime image.
# ---------------------------------------------------------------------------
FROM base AS build

COPY --from=ghcr.io/astral-sh/uv:0.9.7@sha256:ba4857bf2a068e9bc0e64eed8563b065908a4cd6bfb66b531a9c424c8e25e142 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/infrahub-sync/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE.txt ./
COPY infrahub_sync ./infrahub_sync
COPY opsmill_prefect_extras ./opsmill_prefect_extras

# `--no-editable` installs the project as a built distribution, so the runtime
# resolves `infrahub_sync` — and the worker's managed flow — by installed dotted
# identity rather than from a source tree the image would then have to carry.
#
# pytest arrives through `infrahub-sdk[all]`, which the project depends on for its
# runtime. Skipping it here keeps a test framework out of the shipped image without
# touching what a consumer of the published package resolves; nothing the image runs
# imports it.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable --no-install-package pytest --extra service

# ---------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------
FROM base AS runtime

ARG VERSION
ARG REVISION
ARG CREATED

LABEL org.opencontainers.image.title="infrahub-sync" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.source="https://github.com/opsmill/infrahub-sync" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.created="${CREATED}"

# The three directories below are the only paths any command in this image
# writes to, so a deployment can mount them and leave the root filesystem
# read-only. Everything else — the interpreter, the installed distribution, and
# the recorded lock — stays owned by root and unwritable by the runtime user.
RUN groupadd --system --gid 10001 infrahub-sync \
 && useradd --system --uid 10001 --gid 10001 --no-create-home --home-dir /var/lib/infrahub-sync infrahub-sync \
 && install -d -o 10001 -g 10001 -m 0755 \
      /var/lib/infrahub-sync \
      /var/lib/infrahub-sync/prefect \
      /tmp/infrahub-sync

COPY --from=build --chown=0:0 /opt/infrahub-sync/venv /opt/infrahub-sync/venv
COPY --chown=0:0 pyproject.toml uv.lock /opt/infrahub-sync/

ENV PATH=/opt/infrahub-sync/venv/bin:$PATH \
    HOME=/var/lib/infrahub-sync \
    PREFECT_HOME=/var/lib/infrahub-sync/prefect \
    TMPDIR=/tmp/infrahub-sync

USER 10001:10001
WORKDIR /var/lib/infrahub-sync

# The API is the default because it is the safe one: it admits nothing until it
# is configured. The worker, the bootstrap, the CLI, and Python are explicit
# command overrides, which is why this image needs no shell dispatcher.
CMD ["python", "-m", "infrahub_sync.service.serve"]
