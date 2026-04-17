# Build stage: install Python dependencies into an isolated directory
# Builder uses the official Python slim image so we have apt-get for build tools.
# This keeps Chainguard for the production runtime stages while allowing packages
# without pre-built Python 3.14 wheels (e.g. pyarrow) to compile from source.
FROM python:3.14-slim AS builder

WORKDIR /build

# Build tools for packages that require native compilation.
# NOTE: pyarrow (a transitive dep of all-providers extras) does not yet ship Python 3.14
# wheels and its source build is incompatible with 3.14. Until the lockfile is updated to
# a pyarrow release with 3.14 support, the llm/full targets will fail at this step.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc cmake \
    && rm -rf /var/lib/apt/lists/*

# Install Poetry
RUN pip install --no-cache-dir poetry==1.8.2

# Copy dependency files
COPY pyproject.toml poetry.lock* ./

# Build arg for Poetry extras (e.g., "vertex", "anthropic", "all-providers")
ARG POETRY_EXTRAS=""

# Export the lockfile to requirements.txt and install to /deps.
# python -m poetry avoids PATH issues with pip-installed scripts.
# --prefer-binary uses pre-built wheels where available; falls back to source compilation
# (via gcc/cmake installed above) for packages without Python 3.14 wheels (e.g. pyarrow).
RUN if [ -n "$POETRY_EXTRAS" ]; then \
        EXTRA_FLAGS=$(echo "$POETRY_EXTRAS" | tr ',' '\n' | sed 's/^/-E /' | tr '\n' ' '); \
        eval "python -m poetry export --only main $EXTRA_FLAGS -f requirements.txt -o requirements.txt"; \
    else \
        python -m poetry export --only main -f requirements.txt -o requirements.txt; \
    fi \
    && pip install --no-cache-dir --prefer-binary --target=/deps -r requirements.txt


# Runtime stage: minimal hardened image (no shell, runs as nonroot uid 65532).
# Does NOT include git — use the -full image tag when GITHUB_EVIDENCE_ENABLED=true.
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


# Full-featured runtime: same as runtime but on latest-dev, which ships git.
# Use this image (tag suffix: -full) when GITHUB_EVIDENCE_ENABLED=true.
FROM cgr.dev/chainguard/python:latest-dev AS full

WORKDIR /app

ENV PYTHONPATH=/deps
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python", "main.py"]
CMD ["--help"]

COPY --from=builder /deps /deps
COPY main.py ./
COPY agent/ ./agent/


# Debug image: same base as full — shell, apk, and git available for troubleshooting
FROM full AS debug
