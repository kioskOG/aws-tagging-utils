.PHONY: help install dev lint format typecheck test test-cov test-all build run clean

PYTHON ?= python3
PIP    ?= pip

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install production dependencies
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

dev: ## Install dev dependencies (lint, test, type check)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

lint: ## Run ruff linter
	ruff check src/ tests/ mcp_server.py

format: ## Format code with ruff
	ruff format src/ tests/ mcp_server.py

typecheck: ## Run mypy type checker
	mypy src/ --ignore-missing-imports

test: ## Run unit tests (no AWS credentials needed)
	pytest -m "not integration" -v

test-cov: ## Run tests with coverage report
	pytest -m "not integration" --cov=src --cov-report=term-missing --cov-report=html

test-all: ## Run all tests including integration (requires AWS credentials)
	pytest -v

build: ## Build Docker image
	docker build -t aws-tagging-utils:latest .

run: ## Run MCP server locally
	$(PYTHON) mcp_server.py

run-web: ## Run Flask web UI locally
	$(PYTHON) -m flask --app web.app run --host 127.0.0.1 --port 5050 --debug

clean: ## Remove build artifacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf dist/ build/ *.egg-info/
