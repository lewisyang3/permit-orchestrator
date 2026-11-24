.DEFAULT_GOAL := help

COMPOSE := docker compose
SERVICE ?=

.PHONY: setup help config build up down stop start restart rebuild ps logs shell

setup:
	uv sync
	test -f .env || cp .env.example .env

help: ## Show available targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z_-]+:.*## / {printf "  %-12s %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf '\nOptional: SERVICE=fire (or zoning, water, structural, orchestrator)\n'

config: ## Validate the Compose configuration
	$(COMPOSE) config --quiet

build: ## Build images; optionally select SERVICE
	$(COMPOSE) build $(SERVICE)

up: ## Build and start the stack, waiting for healthy services
	$(COMPOSE) up --build -d --wait

down: ## Remove containers and network; preserve SQLite data
	$(COMPOSE) down

stop: ## Stop containers without removing them; optionally select SERVICE
	$(COMPOSE) stop $(SERVICE)

start: ## Start existing containers; optionally select SERVICE
	$(COMPOSE) start $(SERVICE)

restart: ## Restart containers; optionally select SERVICE
	$(COMPOSE) restart $(SERVICE)

rebuild: ## Rebuild and recreate the stack; preserve SQLite data
	$(COMPOSE) up --build --force-recreate -d --wait

ps: ## Show container status
	$(COMPOSE) ps

logs: ## Follow recent logs; optionally select SERVICE
	$(COMPOSE) logs --tail=100 --follow $(SERVICE)

shell: ## Open a container shell; requires SERVICE
	@test -n "$(SERVICE)" || { echo "Usage: make shell SERVICE=fire"; exit 1; }
	$(COMPOSE) exec $(SERVICE) sh
