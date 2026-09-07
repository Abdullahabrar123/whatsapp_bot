.PHONY: help install validate test lint typecheck security run migrate up down seed import-contacts dry-run-campaign

PYTHON ?= python

help:
	@echo "WhatsApp Business Platform Chatbot"
	@echo ""
	@echo "Available commands:"
	@echo "  make install         Install python dependencies in virtual environment"
	@echo "  make validate        Validate YAML business configs and .env settings"
	@echo "  make test            Run the full automated test suite"
	@echo "  make lint            Run ruff code linter"
	@echo "  make typecheck       Run mypy strict type checker"
	@echo "  make security        Run bandit and pip-audit security scans"
	@echo "  make run             Start local FastAPI dev server"
	@echo "  make migrate         Run alembic database migrations"
	@echo "  make seed            Seed demo contacts and conversations"
	@echo "  make import-contacts Import contacts from data/recipients.csv"
	@echo "  make up              Start full docker-compose stack"
	@echo "  make down            Stop docker-compose stack"

install:
	pip install -e ".[dev]"

validate:
	$(PYTHON) scripts/validate_config.py

test:
	pytest --cov=app --cov-report=term-missing tests/

lint:
	ruff check .

typecheck:
	mypy app/

security:
	bandit -r app/
	pip-audit

run:
	$(PYTHON) main.py

migrate:
	alembic upgrade head

seed:
	$(PYTHON) scripts/seed_demo.py

import-contacts:
	$(PYTHON) scripts/import_contacts.py --file data/recipients.csv

dry-run-campaign:
	$(PYTHON) scripts/send_campaign.py --dry-run

up:
	docker compose up -d

down:
	docker compose down
