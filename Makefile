SHELL := /bin/bash

PYTHON ?= python3.13
HOST ?= 127.0.0.1
BACKEND_PORT ?= 8100
AI_BACKEND_PORT ?= 8000
FACADE_PORT ?= 8200
FRONTEND_PORT ?= 5173

BACKEND_PYTHON := services/backend/.venv/bin/python
FACADE_PYTHON := services/backend-facade/.venv/bin/python
AI_BACKEND_PYTHON := services/ai-backend/.venv/bin/python
SERVICE_CONTRACTS_PATH := ../../packages/service-contracts/src

.PHONY: help setup setup-node setup-python check-local-env check-provider-key dev prod prod-build check-prod-env docker-dev docker-dev-down test

help:
	@echo "Enterprise Search make targets"
	@echo
	@echo "  make setup            Install npm deps and Python service venvs"
	@echo "  make dev              Run local end-to-end stack on 127.0.0.1"
	@echo "  make docker-dev       Run Docker dev stack on http://127.0.0.1:8080"
	@echo "  make docker-dev-down  Stop Docker dev stack"
	@echo "  make prod             Build production artifacts after prod env checks"
	@echo "  make test             Run focused auth/runtime tests"

setup: setup-node setup-python

setup-node:
	npm install

setup-python:
	cd services/backend && \
		$(PYTHON) -m venv .venv && \
		.venv/bin/python -m pip install --upgrade pip && \
		.venv/bin/python -m pip install -r requirements.txt
	cd services/backend-facade && \
		$(PYTHON) -m venv .venv && \
		.venv/bin/python -m pip install --upgrade pip && \
		.venv/bin/python -m pip install -r requirements.txt
	cd services/ai-backend && \
		$(PYTHON) -m venv .venv && \
		.venv/bin/python -m pip install --upgrade pip && \
		.venv/bin/python -m pip install -r requirements.txt && \
		[ -f .env ] || cp env_example .env

check-local-env:
	@test -x "$(BACKEND_PYTHON)" || (echo "Missing services/backend/.venv. Run: make setup" && exit 1)
	@test -x "$(FACADE_PYTHON)" || (echo "Missing services/backend-facade/.venv. Run: make setup" && exit 1)
	@test -x "$(AI_BACKEND_PYTHON)" || (echo "Missing services/ai-backend/.venv. Run: make setup" && exit 1)
	@test -d node_modules || (echo "Missing node_modules. Run: make setup" && exit 1)

check-provider-key:
	@if [ -z "$${OPENAI_API_KEY}$${ANTHROPIC_API_KEY}$${GOOGLE_API_KEY}" ]; then \
		if [ -f services/ai-backend/.env ]; then set -a; source services/ai-backend/.env; set +a; fi; \
		if [ -z "$${OPENAI_API_KEY}$${ANTHROPIC_API_KEY}$${GOOGLE_API_KEY}" ]; then \
			echo "Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY before running the agent."; \
			echo "You can put it in services/ai-backend/.env for local dev."; \
			exit 1; \
		fi; \
	fi

dev: check-local-env check-provider-key
	@echo "Starting Enterprise Search dev stack"
	@echo "UI:      http://$(HOST):$(FRONTEND_PORT)"
	@echo "Facade:  http://$(HOST):$(FACADE_PORT)"
	@echo "Backend: http://$(HOST):$(BACKEND_PORT)"
	@echo "AI API:  http://$(HOST):$(AI_BACKEND_PORT)"
	@pids=""; \
	cleanup() { \
		echo; echo "Stopping Enterprise Search dev stack"; \
		[ -n "$$pids" ] && kill $$pids 2>/dev/null || true; \
		wait $$pids 2>/dev/null || true; \
	}; \
	trap cleanup INT TERM EXIT; \
	(cd services/backend && \
		BACKEND_ENVIRONMENT=development \
		MCP_TOKEN_VAULT_PROVIDER=local \
		PYTHONPATH=src:$(SERVICE_CONTRACTS_PATH) \
		.venv/bin/python -m uvicorn backend_app.app:app --host $(HOST) --port $(BACKEND_PORT)) & pids="$$pids $$!"; \
	(cd services/ai-backend && \
		RUNTIME_ENVIRONMENT=development \
		RUNTIME_STORE_BACKEND=in_memory \
		RUNTIME_START_IN_PROCESS_WORKER=true \
		MCP_BACKEND_REGISTRY_URL=http://$(HOST):$(BACKEND_PORT) \
		SKILLS_BACKEND_REGISTRY_URL=http://$(HOST):$(BACKEND_PORT) \
		PYTHONPATH=src:$(SERVICE_CONTRACTS_PATH) \
		.venv/bin/python -m uvicorn runtime_api.app:app --host $(HOST) --port $(AI_BACKEND_PORT)) & pids="$$pids $$!"; \
	(cd services/backend-facade && \
		FACADE_ENVIRONMENT=development \
		DEV_AUTH_BYPASS=true \
		FACADE_DEV_ORG_ID=org_123 \
		FACADE_DEV_USER_ID=user_123 \
		BACKEND_URL=http://$(HOST):$(BACKEND_PORT) \
		AI_BACKEND_URL=http://$(HOST):$(AI_BACKEND_PORT) \
		PYTHONPATH=src:$(SERVICE_CONTRACTS_PATH) \
		.venv/bin/python -m uvicorn backend_facade.app:app --host $(HOST) --port $(FACADE_PORT)) & pids="$$pids $$!"; \
	(npm run dev --workspace @enterprise-search/frontend -- --host $(HOST) --port $(FRONTEND_PORT)) & pids="$$pids $$!"; \
	wait $$pids

docker-dev: check-provider-key
	docker compose -f docker-compose.dev.yml up --build

docker-dev-down:
	docker compose -f docker-compose.dev.yml down

check-prod-env:
	@test -n "$$ENTERPRISE_AUTH_SECRET" || (echo "ENTERPRISE_AUTH_SECRET is required for make prod" && exit 1)
	@test -n "$$ENTERPRISE_SERVICE_TOKEN" || (echo "ENTERPRISE_SERVICE_TOKEN is required for make prod" && exit 1)
	@test -n "$$MCP_TOKEN_VAULT_SECRET" || (echo "MCP_TOKEN_VAULT_SECRET is required for make prod" && exit 1)
	@test -n "$${OPENAI_API_KEY}$${ANTHROPIC_API_KEY}$${GOOGLE_API_KEY}" || (echo "Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GOOGLE_API_KEY for make prod" && exit 1)
	@if [ "$$DEV_AUTH_BYPASS" = "true" ]; then \
		echo "DEV_AUTH_BYPASS must not be true for make prod"; \
		exit 1; \
	fi

prod: check-prod-env prod-build
	@echo
	@echo "Production artifacts built with dev auth disabled."
	@echo "Deploy them with your production orchestrator and managed secrets."
	@echo "Note: backend production runtime still requires a persistent MCP registry store and managed token-vault adapter."

prod-build:
	npm run build --workspaces --if-present
	docker build -f services/backend/Dockerfile -t enterprise-search-backend:prod .
	docker build -f services/ai-backend/Dockerfile -t enterprise-search-ai-backend:prod .
	docker build -f services/backend-facade/Dockerfile -t enterprise-search-backend-facade:prod .
	docker build -f apps/frontend/Dockerfile -t enterprise-search-frontend:prod .

test:
	cd services/backend && PYTHONPATH=src:$(SERVICE_CONTRACTS_PATH) .venv/bin/python -m pytest tests/test_mcp_api_flow.py tests/test_skills_api_flow.py
	cd services/backend-facade && PYTHONPATH=src:$(SERVICE_CONTRACTS_PATH) .venv/bin/python -m pytest tests/test_facade_settings.py
	cd services/ai-backend && PYTHONPATH=src:$(SERVICE_CONTRACTS_PATH) .venv/bin/python -m pytest tests/unit/agent_runtime/mcp/test_mcp_auth_tool.py tests/unit/agent_runtime/skills/test_virtual_skills.py tests/unit/agent_runtime/memory/test_context_memory_management.py tests/unit/agent_runtime/agent/test_runtime_factory.py tests/unit/agent_runtime/agent/test_skills_runtime_factory.py
