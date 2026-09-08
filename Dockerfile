# syntax=docker/dockerfile:1

# --- Etapa de construcción: las dependencias se compilan aparte para que la
# imagen final no arrastre compiladores ni cabeceras de desarrollo. ---
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt


# --- Imagen final ---
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 finai

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=finai:finai alembic.ini pyproject.toml ./
COPY --chown=finai:finai alembic ./alembic
COPY --chown=finai:finai app ./app

RUN mkdir -p /app/var/receipts && chown -R finai:finai /app/var

# Nunca como root: un desbordamiento en la API no debe dar la máquina entera.
USER finai

EXPOSE 8000

# Las plataformas gestionadas (Render, Fly...) imponen el puerto vía $PORT.
ENV PORT=8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://localhost:${PORT}/api/v1/health" || exit 1

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --proxy-headers"]
