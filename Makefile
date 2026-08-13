.PHONY: help install install-dev install-embeddings install-rag \
       dev server worker test test-fast test-cov lint format type-check \
       docker-up docker-down docker-logs docker-ps \
       backup restore clean clean-cache clean-db \
       doctor migrate

# Default Python interpreter
PYTHON ?= python
PIP ?= $(PYTHON) -m pip
PYTEST ?= $(PYTHON) -m pytest

# Project root
ROOT_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))

help: ## Show this help
	@echo "agentbase — Make targets"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install agentbase (production deps only)
	$(PIP) install -e .

install-dev: ## Install agentbase with development dependencies
	$(PIP) install -e ".[dev]"

install-embeddings: ## Install with embedding providers (sentence-transformers)
	$(PIP) install -e ".[embeddings]"

install-rag: ## Install with RAG document parsers (PDF, DOCX, Excel, PPTX)
	$(PIP) install -e ".[rag]"

dev: install-dev ## Install dev deps and run dev server
	$(PYTHON) -m agentbase serve --reload --host 0.0.0.0 --port 8000

server: ## Start the API server (production mode)
	$(PYTHON) -m agentbase serve --host 0.0.0.0 --port 8000

worker: ## Start a background task queue worker
	$(PYTHON) -m agentbase worker

test: ## Run all tests
	$(PYTEST) tests/ -v

test-fast: ## Run tests excluding slow/integration tests
	$(PYTEST) tests/ -v -m "not slow and not integration"

test-cov: ## Run tests with coverage report
	$(PYTEST) tests/ --cov=src/agentbase --cov-report=term-missing --cov-report=html

lint: ## Run linter (ruff)
	$(PYTHON) -m ruff check src/ tests/

format: ## Format code (ruff)
	$(PYTHON) -m ruff format src/ tests/
	$(PYTHON) -m ruff check --fix src/ tests/

type-check: ## Type check (mypy, optional)
	$(PYTHON) -m mypy src/agentbase --ignore-missing-imports || true

# ─── Docker ──────────────────────────────────────────────────

docker-up: ## Start all services via docker compose
	docker compose up -d

docker-down: ## Stop all docker compose services
	docker compose down

docker-logs: ## Show docker compose logs (follow)
	docker compose logs -f

docker-ps: ## Show running docker containers
	docker compose ps

# ─── Database ─────────────────────────────────────────────────

backup: ## Backup database to backup.sql
	$(PYTHON) -m agentbase backup -o backup.sql --format sql

restore: ## Restore database from backup.sql
	$(PYTHON) -m agentbase restore backup.sql --format sql

migrate: ## Run database migrations (if available)
	$(PYTHON) -m agentbase migrate || echo "No migrations configured."

# ─── Utilities ───────────────────────────────────────────────

doctor: ## Run health checks
	$(PYTHON) -m agentbase doctor


clean: ## Remove build artifacts and cache files
	rm -rf build/ dist/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true

clean-cache: ## Clear all Python caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true

clean-db: ## Remove local SQLite databases (dev only!)
	rm -f data/*.db data/*.db-journal 2>/dev/null || true
