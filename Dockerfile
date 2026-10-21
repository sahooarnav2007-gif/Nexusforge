# syntax=docker/dockerfile:1
#
# NexusForge API image.
#
# Single-stage build on python:3.11-slim. The agent worker runs IN-PROCESS
# (one uvicorn worker), so a single container is the whole backend. The
# `process` sandbox backend (rlimits) works out of the box; the `docker`
# sandbox backend additionally needs the Docker CLI + socket available to the
# container (see README "Sandbox backends").

FROM python:3.11-slim

# Unbuffered output, no .pyc noise, quieter pip.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# git is used by the gitops service; curl powers the container healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application source (only what the runtime + tests need).
COPY core/ core/
COPY services/ services/
COPY api/ api/
COPY apps/ apps/
COPY scripts/ scripts/
COPY demo/ demo/
COPY tests/ tests/
COPY pytest.ini .

# Runtime data dir: SQLite DB, agent workspaces, and the audit log live here.
# The app auto-creates its tables at startup; the directory just needs to
# exist and be writable by the non-root user.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin nexusforge \
    && mkdir -p /app/var \
    && chown -R nexusforge:nexusforge /app/var

USER nexusforge

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "apps.api.main"]