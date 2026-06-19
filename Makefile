.PHONY: lock sync test lint format check run sync-requirements up down logs build

lock:
	uv lock

sync:
	uv sync

test:
	uv run pytest tests/test_deterministic.py -v

lint:
	uv run ruff check .

format:
	uv run ruff format .

fix:
	uv run ruff check --fix . && uv run ruff format .

check: lint test

run:
	uv run uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Regenerate requirements.txt from uv.lock (run before build if deps changed)
sync-requirements:
	uv pip compile pyproject.toml -o requirements.txt

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f
