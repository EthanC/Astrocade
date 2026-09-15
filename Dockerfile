FROM python:alpine AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python:alpine AS runtime

ENV DATABASE_PATH=/astrocade/astrocade.db \
    PATH="/opt/venv/bin:$PATH" \
    PGID=1000 \
    PUID=1000 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --from=builder --chown=0:0 /build/.venv /opt/venv
COPY --chown=0:0 astrocade.py ./
COPY --chown=0:0 core ./core
COPY --chown=0:0 extensions ./extensions
COPY --chown=0:0 assets/astrocade_icon.png assets/astrocade_logo.png assets/wordle_icon.png ./assets/
COPY --chown=0:0 --chmod=0555 docker/entrypoint.py docker/healthcheck.py /usr/local/libexec/

RUN mkdir -p /astrocade \
    && chmod 0755 /astrocade \
    && chmod -R go-w /app /opt/venv /usr/local/libexec

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "/usr/local/libexec/healthcheck.py"]

ENTRYPOINT ["python", "/usr/local/libexec/entrypoint.py"]
CMD ["python", "-OO", "astrocade.py"]
