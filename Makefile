.PHONY: check check-live check-frontend
check:
	uv run ruff check src tests migrations
	uv run ruff format --check src tests migrations
	uv run mypy src
	uv run pytest -m "not live"
check-live:
	uv run pytest

check-frontend:
	cd frontend && npm ci && npm run check && npm run build
