# Vector Pacing — common tasks. The demo is a static React app (Node >= 20.19); no backend is needed.
PAGES_BASE ?= /Vector-Pacing/

.PHONY: install dev build preview pages-build pages-preview test lint check clean \
        reference-install reference-test

install:
	cd frontend && npm ci

# Development server with hot reload at http://127.0.0.1:5173
dev:
	cd frontend && npm run dev -- --host 127.0.0.1

build:
	cd frontend && npm run build

# Serve the production build at http://127.0.0.1:4173
preview: build
	cd frontend && npm run preview -- --host 127.0.0.1

# Build and serve exactly as GitHub Pages will: http://127.0.0.1:4173/Vector-Pacing/
pages-build:
	cd frontend && VITE_BASE_PATH=$(PAGES_BASE) npm run build

pages-preview: pages-build
	cd frontend && npm run preview -- --host 127.0.0.1 --base $(PAGES_BASE)

test:
	cd frontend && npm test

lint:
	cd frontend && npm run lint && npm run typecheck

# Everything the Pages workflow runs before deploying.
check: lint test build

clean:
	rm -rf frontend/dist

# Optional: the original Python implementation, kept as a reference (not used by the demo).
reference-install:
	python3 -m venv backend/.venv
	backend/.venv/bin/pip install -e "backend[dev]"

reference-test:
	cd backend && .venv/bin/ruff check . && .venv/bin/mypy && .venv/bin/pytest -q
