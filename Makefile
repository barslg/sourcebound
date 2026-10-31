.PHONY: check check-live check-public check-frontend ci-local
check:
	uv run ruff check src tests migrations scripts frontend/e2e
	uv run ruff format --check src tests migrations scripts frontend/e2e
	uv run mypy src
	uv run pytest -m "not live"
	uv run python scripts/check_layers.py
	uv run python scripts/check_public_tree.py
	uv run python scripts/check_routes.py
check-live:
	uv run pytest
check-public:
	uv run python scripts/check_public_tree.py
check-frontend:
	if [ -f frontend/package.json ]; then \
		cd frontend && npm ci && npm run check && npm run build; \
	else \
		echo "no frontend/package.json; skipped"; \
	fi
ci-local:
	scripts/ci_local.sh
