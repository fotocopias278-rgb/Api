.PHONY: help install dev test test-pg smoke-vision lint fmt migrate revision run worker up down logs clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

help:  ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Crea el entorno virtual e instala dependencias
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt

test:  ## Ejecuta la batería de pruebas
	$(PY) -m pytest

test-pg:  ## Ejecuta las pruebas contra PostgreSQL (usa TEST_DATABASE_URL)
	TEST_DATABASE_URL=$${TEST_DATABASE_URL:-postgresql+asyncpg://finai:finai@localhost:5432/finai} \
		$(PY) -m pytest

smoke-vision:  ## Prueba la extracción contra la API real (requiere ANTHROPIC_API_KEY)
	$(PY) scripts/smoke_vision.py

lint:  ## Analiza el código con ruff
	$(VENV)/bin/ruff check app tests alembic

fmt:  ## Corrige automáticamente lo que ruff sabe arreglar
	$(VENV)/bin/ruff check app tests alembic --fix
	$(VENV)/bin/ruff format app tests alembic

migrate:  ## Aplica las migraciones pendientes
	$(VENV)/bin/alembic upgrade head

revision:  ## Genera una migración (uso: make revision M="mensaje")
	$(VENV)/bin/alembic revision --autogenerate -m "$(M)"

run:  ## Arranca la API en modo desarrollo
	$(VENV)/bin/uvicorn app.main:app --reload --port 8000

worker:  ## Arranca un worker de Celery
	$(VENV)/bin/celery -A app.workers.celery_app.celery_app worker --loglevel=info

up:  ## Levanta toda la pila con Docker Compose
	docker compose up --build -d

down:  ## Detiene la pila
	docker compose down

logs:  ## Sigue los logs de la API
	docker compose logs -f api

clean:  ## Elimina artefactos temporales
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache htmlcov .coverage
