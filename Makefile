# Vector Pacing — common tasks. Requires Python >= 3.11 and Node >= 20.
PY ?= python3
VENV := backend/.venv
BIN := $(VENV)/bin

.PHONY: install install-backend install-frontend dev backend frontend build serve test test-backend test-frontend lint check clean

install: install-backend install-frontend

install-backend:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e "backend[dev]"

install-frontend:
	cd frontend && npm ci

# Development: API on :8000 and Vite dev server on :5173 (proxying /api).
dev:
	@echo "API: http://127.0.0.1:8000  |  App: http://127.0.0.1:5173"
	@trap 'kill 0' INT TERM EXIT; \
	  $(BIN)/uvicorn vector_pacing.api:app --app-dir backend --host 127.0.0.1 --port 8000 --reload & \
	  (cd frontend && npm run dev -- --host 127.0.0.1) & \
	  wait

backend:
	$(BIN)/uvicorn vector_pacing.api:app --app-dir backend --host 127.0.0.1 --port 8000 --reload

frontend:
	cd frontend && npm run dev -- --host 127.0.0.1

build:
	cd frontend && npm run build

# Single-process demo: build the UI, serve UI + API at http://127.0.0.1:8000
serve: build
	$(BIN)/uvicorn vector_pacing.api:app --app-dir backend --host 127.0.0.1 --port 8000

test: test-backend test-frontend

test-backend:
	cd backend && .venv/bin/pytest -q

test-frontend:
	cd frontend && npm test

lint:
	cd backend && .venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy
	cd frontend && npm run lint && npm run typecheck

# Everything CI runs.
check: lint test build

clean:
	rm -rf frontend/dist backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache
