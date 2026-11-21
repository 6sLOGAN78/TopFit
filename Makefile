DC := docker compose -f compose.yaml -f compose.dev.yaml
DEPLOY_USER ?= deploy
DEPLOY_KEY ?= $(HOME)/.ssh/ranker_deploy
IMAGE_TAG ?= sha-$(shell git rev-parse --short=7 HEAD)

.PHONY: help dev down db test lint fmt typecheck web-check build selfcheck logs psql deploy smoke
.DEFAULT_GOAL := help

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

dev: ## Full local stack with hot reload (http://localhost:3000)
	$(DC) up --build

down: ## Stop the local stack
	$(DC) down

db: ## Start only Postgres (native dev path)
	$(DC) up -d db

test: ## Backend tests (needs Docker for testcontainers)
	cd backend && uv run pytest -q

lint: ## Ruff + web lint/format checks
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd web && npm run lint && npm run format:check

fmt: ## Auto-format backend and web
	cd backend && uv run ruff format . && uv run ruff check --fix .
	cd web && npm run format

typecheck: ## pyright + tsc
	cd backend && uv run pyright
	cd web && npm run typecheck

web-check: ## Web lint, typecheck, build
	cd web && npm run lint && npm run typecheck && npm run build

build: ## Build local api and web images
	docker build -t ranker-api:dev backend
	docker build -t ranker-web:dev web

selfcheck: ## Prove the baked model loads with no network
	docker run --rm --network none ranker-api:dev python -m ranker.selfcheck

logs: ## Follow compose logs
	$(DC) logs -f

psql: ## psql into the dev database
	$(DC) exec db psql -U ranker -d ranker

# Manual deploy fallback (D-14). IMAGE_TAG must already exist in GHCR (pushed by CI);
# the command is identical to what CD runs (D-13).
deploy: ## Deploy IMAGE_TAG to DEPLOY_HOST over ssh
	@test -n "$(DEPLOY_HOST)" || { echo "usage: make deploy DEPLOY_HOST=<host> [DEPLOY_USER=deploy] [DEPLOY_KEY=~/.ssh/ranker_deploy] [IMAGE_TAG=sha-xxxxxxx]"; exit 1; }
	scp -i $(DEPLOY_KEY) deploy/compose.prod.yaml deploy/Caddyfile deploy/deploy.sh deploy/smoke.sh deploy/measure-memory.sh $(DEPLOY_USER)@$(DEPLOY_HOST):/srv/ranker/
	ssh -i $(DEPLOY_KEY) $(DEPLOY_USER)@$(DEPLOY_HOST) "cd /srv/ranker && IMAGE_TAG=$(IMAGE_TAG) ./deploy.sh"
	$(MAKE) smoke

smoke: ## Smoke-test PUBLIC_HOSTNAME
	@test -n "$(PUBLIC_HOSTNAME)" || { echo "usage: make smoke PUBLIC_HOSTNAME=<host>"; exit 1; }
	bash deploy/smoke.sh $(PUBLIC_HOSTNAME)
