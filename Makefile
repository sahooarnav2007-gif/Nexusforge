# NexusForge developer convenience targets.

PYTHON ?= python
PIP    ?= pip

.PHONY: install run test lint format demo docker-build docker-up docker-down

## install: install Python dependencies (dev + runtime).
install:
	$(PIP) install -r requirements.txt

## run: start the API (uvicorn) on 127.0.0.1:8000 with the in-process worker.
run:
	$(PYTHON) -m apps.api.main

## test: run the pytest suite (asyncio_mode=auto, testpaths=tests).
test:
	$(PYTHON) -m pytest

## lint: ruff check (rules in ruff.toml; pre-existing debt grandfathered there).
lint:
	$(PYTHON) -m ruff check .

## format: ruff format the codebase.
format:
	$(PYTHON) -m ruff format .

## demo: drive the full pipeline end-to-end against a running API.
##       Start the API first: make run  (then in another shell: make demo)
demo:
	$(PYTHON) scripts/demo.py

## docker-build: build the API image.
docker-build:
	docker build -t nexusforge-api:latest .

## docker-up: build + start the full stack (api + optional db/redis).
docker-up:
	docker compose up --build

## docker-down: stop the stack (add -v to also remove volumes).
docker-down:
	docker compose down