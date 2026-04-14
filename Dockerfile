# Build stage: install Python dependencies into an isolated directory
FROM cgr.dev/chainguard/python:latest-dev AS builder

WORKDIR /build

# Install Poetry (dev image ships with pip)
RUN pip install --no-cache-dir poetry==1.8.2

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Build arg for Poetry extras (e.g., "vertex", "anthropic", "all-providers")
ARG POETRY_EXTRAS=""

# Export the lockfile to requirements.txt and install to /deps.
# Using --target avoids venv symlink issues across image stages.
RUN if [ -n "$POETRY_EXTRAS" ]; then \
        EXTRA_FLAGS=$(echo "$POETRY_EXTRAS" | tr ',' '\n' | sed 's/^/-E /' | tr '\n' ' '); \
        eval "poetry export --only main $EXTRA_FLAGS -f requirements.txt -o requirements.txt"; \
    else \
        poetry export --only main -f requirements.txt -o requirements.txt; \
    fi \
    && pip install --no-cache-dir --target=/deps -r requirements.txt


# Runtime stage: minimal hardened image (no shell, runs as nonroot uid 65532)
# NOTE: this image does not include git. If GITHUB_EVIDENCE_ENABLED=true is
# needed in production, swap this base to cgr.dev/chainguard/python:latest-dev.
FROM cgr.dev/chainguard/python:latest AS runtime

WORKDIR /app

ENV PYTHONPATH=/deps
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Ensure Kubernetes `args:` works (it overrides CMD but keeps ENTRYPOINT).
ENTRYPOINT ["python", "main.py"]
# Default behavior if no args are provided.
CMD ["--help"]

COPY --from=builder /deps /deps
COPY main.py ./
COPY agent/ ./agent/


# Debug image: dev variant retains shell, apk, and git for live troubleshooting
FROM cgr.dev/chainguard/python:latest-dev AS debug

WORKDIR /app

ENV PYTHONPATH=/deps
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "main.py"]
CMD ["--help"]

COPY --from=builder /deps /deps
COPY main.py ./
COPY agent/ ./agent/


# Default output image stays slim (same as runtime)
FROM runtime AS final
