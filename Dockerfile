FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install dependencies (cached layer — only rebuilds when lock file changes)
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Run unprivileged. The code and .venv stay root-owned, so the app can't modify them;
# only /app itself is writable, for the SQLite file when DB_TYPE=sqlite.
RUN useradd --system --uid 1001 --no-create-home latterboard \
    && chown latterboard /app
USER latterboard

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Wait for the database, bring its schema up to date, then serve. A failed migration
# stops the container rather than serving against the wrong schema.
CMD ["sh", "-c", "python -m app.backend_pre_start && alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
