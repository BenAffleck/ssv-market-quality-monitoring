FROM python:3.12-slim

# Install uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml ./
COPY src ./src
COPY README.md ./README.md
RUN uv pip install --system --no-cache .

# Application assets (config + migrations) used at runtime.
COPY config ./config
COPY migrations ./migrations

ENV SSV_MQM_CONFIG=/app/config/config.yaml \
    SSV_MQM_MIGRATIONS=/app/migrations/001_init.sql \
    PYTHONUNBUFFERED=1

# Default: run the collector+sampler. The aggregator service overrides the command.
CMD ["python", "-m", "ssv_mqm.main"]
