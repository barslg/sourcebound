.PHONY: check check-live check-public check-frontend ci-local gates check-contract
check:
	uv run ruff check src tests migrations scripts
	uv run ruff format --check src tests migrations scripts
	uv run mypy src
	uv run pytest -m "not live"
	uv run python scripts/run_gates.py
gates:
	uv run python scripts/run_gates.py
check-contract:
	uv run python scripts/check_contract.py
check-live:
	uv run pytest
check-public:
	uv run python scripts/check_public_tree.py
check-frontend:
	if [ -f frontend/package.json ]; then \
		cd frontend && npm run typecheck && npm run build; \
	else \
		echo "no frontend/package.json; skipped"; \
	fi
ci-local:
	scripts/ci_local.sh
