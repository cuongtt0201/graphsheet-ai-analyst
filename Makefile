.PHONY: help up down restart build build-sandbox logs status test clean

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  up              Start all services (Neo4j, Backend, Frontend) in background"
	@echo "  down            Stop all running containers"
	@echo "  restart         Restart backend and frontend containers"
	@echo "  build           Build/Rebuild all application Docker images"
	@echo "  build-sandbox   Build isolated code execution sandbox image (ai-dashboard-sandbox)"
	@echo "  logs            Tail logs across all containers"
	@echo "  status          Display status of all containers"
	@echo "  test            Run backend automated pytest suite"
	@echo "  clean           Stop and wipe database volumes"

up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart backend frontend

build:
	docker compose build

build-sandbox:
	docker build -f backend/Dockerfile.sandbox -t ai-dashboard-sandbox backend/

logs:
	docker compose logs -f

status:
	docker compose ps

test:
	pytest backend/tests/ -v

clean:
	docker compose down -v
