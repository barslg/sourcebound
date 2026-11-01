# Single baked image for api / migrate / worker. No bind-mount of code.
ARG PYTHON_VERSION=3.12

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS uv

FROM python:${PYTHON_VERSION}-slim-bookworm AS builder
COPY --from=uv /usr/local/bin/uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock .python-version ./
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./
# prompts/ is private until publication, so a fresh clone has none: the glob keeps the
# build going (alembic.ini always matches) and docker-entrypoint.sh refuses to START
# the product without them. Never run the product on stubs.
COPY alembic.ini prompts*/ /stage/
RUN mkdir -p prompts \
    && find /stage -mindepth 1 -maxdepth 1 ! -name alembic.ini -exec mv {} prompts/ \; \
    && rm -rf /stage
RUN uv sync --frozen --no-dev

FROM python:${PYTHON_VERSION}-slim-bookworm AS runtime
RUN useradd --uid 10001 --create-home appuser
WORKDIR /app
ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONPATH="/app/src" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
COPY --from=builder --chown=10001:10001 /app/src /app/src
COPY --from=builder --chown=10001:10001 /app/migrations /app/migrations
COPY --from=builder --chown=10001:10001 /app/prompts /app/prompts
COPY --from=builder --chown=10001:10001 /app/alembic.ini /app/alembic.ini
COPY --chown=10001:10001 docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod 0555 /app/docker-entrypoint.sh
USER 10001
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
