# syntax=docker/dockerfile:1

# --- Build stage -----------------------------------------------------------
# python-osc/requests/pyResi are all pure Python, but the pyresi dependency
# is pinned to a git commit (see uv.lock), so this stage needs git to
# resolve/fetch it. Nothing here ends up in the final image.
FROM python:3.14-slim AS builder

RUN apt-get update && apt-get install --no-install-recommends -y git \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Resolve/build dependencies first, from the lockfile alone, so this layer
# only invalidates when pyproject.toml/uv.lock change, not on every source
# edit.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --no-dev

# Now add the actual project source and install it into the same venv.
COPY README.md ./
COPY src ./src
RUN uv sync --locked --no-dev

# --- Runtime stage -----------------------------------------------------------
FROM python:3.14-slim AS runtime

RUN useradd --create-home --shell /usr/sbin/nologin resi
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER resi

# OSC is UDP; both ports are configurable at runtime via OSC_LISTEN_PORT /
# OSC_REPLY_PORT (see README's "Running" section) — these EXPOSE lines just
# document the defaults.
EXPOSE 9000/udp
EXPOSE 9001/udp

ENTRYPOINT ["resi-cuecontrol"]
