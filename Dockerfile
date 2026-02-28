FROM python:3.13-slim AS runtime

WORKDIR /app

# System deps (certs for HTTPS endpoints / AWS)
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.2

# Copy dependency files
COPY pyproject.toml poetry.lock* /app/

# Build arg for Poetry extras (e.g., "vertex", "anthropic", "all-providers")
ARG POETRY_EXTRAS=""

# Install dependencies only (no dev dependencies)
# If POETRY_EXTRAS is set, install with extras: poetry install --only main -E vertex
RUN poetry config virtualenvs.create false \
    && if [ -n "$POETRY_EXTRAS" ]; then \
        echo "Installing with extras: $POETRY_EXTRAS"; \
        poetry install --only main -E "$POETRY_EXTRAS" --no-interaction --no-ansi; \
    else \
        echo "Installing without LLM extras (deterministic mode only)"; \
        poetry install --only main --no-interaction --no-ansi; \
    fi

# Copy application code
COPY main.py /app/main.py
COPY agent/ /app/agent/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Ensure Kubernetes `args:` works (it overrides CMD but keeps ENTRYPOINT).
ENTRYPOINT ["python", "main.py"]
# Default behavior if no args are provided.
CMD ["--help"]

# Optional debug image (keeps python deps identical, adds just bash+curl)
FROM runtime AS debug
RUN apt-get update && apt-get install -y --no-install-recommends bash curl && rm -rf /var/lib/apt/lists/*

# Default output image stays slim (same as runtime)
FROM runtime AS final
