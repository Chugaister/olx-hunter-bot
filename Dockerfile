# OLX Hunter bot (FastAPI webhook + Claude Agent SDK).
# The Agent SDK wheel bundles Claude Code as a native glibc binary, so no Node is
# needed, but the base must be glibc-based (Debian slim, not Alpine).

# ---- build: resolve and install dependencies + the project into /app/.venv ----
FROM python:3.14-slim-trixie AS build

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first: this layer is reused until pyproject.toml / uv.lock change.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Then the project itself, installed as a regular (non-editable) package.
COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# ---- runtime ----
FROM python:3.14-slim-trixie

# uid/gid 1000 matches the usual host user, so a bind-mounted ./data stays writable.
RUN groupadd --gid 1000 bot && useradd --uid 1000 --gid bot --create-home bot

COPY --from=build --chown=bot:bot /app/.venv /app/.venv

# HOME lives on the data volume: Claude Code keeps its sessions in ~/.claude, and the
# chat agent resumes them, so conversations survive container restarts.
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    HOME=/data/home \
    HOST=0.0.0.0 \
    PORT=8000

USER bot
WORKDIR /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

CMD ["sh", "-c", "mkdir -p \"$HOME\" && exec olx-hunter-bot"]
