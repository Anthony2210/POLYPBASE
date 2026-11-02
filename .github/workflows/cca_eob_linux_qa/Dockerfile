# syntax=docker/dockerfile:1
# Polypbase CCA-EOB Linux CPython 3.13 Isolated QA Environment
# Reproducible Debian Bookworm environment with official CPython 3.13
# Pinned to python:3.13-slim-bookworm@sha256:70bda1e795ded2a684ef9815955e05829b14ee5bb5c3eb2a940648e88f10fce9 (amd64)

FROM python:3.13-slim-bookworm@sha256:70bda1e795ded2a684ef9815955e05829b14ee5bb5c3eb2a940648e88f10fce9

# Unprivileged QA service account
ARG QA_UID=10001
ARG QA_GID=10001

# Enforce strict offline execution and isolation defaults
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHON_DOTENV_DISABLED=1 \
    POSTGRES_DB="" \
    DJANGO_SETTINGS_MODULE=config.test_settings \
    REQUIRE_NETWORK_ISOLATION=1

# Install required system packages for dependency installation
RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager (pinned to 0.13.6)
RUN pip install --no-cache-dir uv==0.13.6

# Create unprivileged QA runner account and target directory
RUN groupadd --gid ${QA_GID} qa-runner \
    && useradd --uid ${QA_UID} --gid ${QA_GID} --create-home --home-dir /home/qa-runner \
        --shell /bin/bash qa-runner \
    && mkdir -p /app \
    && chown -R qa-runner:qa-runner /app /home/qa-runner

WORKDIR /app

# Copy lockfile and dependency declaration first for optimal layer caching
COPY --chown=qa-runner:qa-runner pyproject.toml uv.lock /app/

# Switch to unprivileged user to create virtualenv and install dependencies
USER qa-runner

# Create real uv virtual environment with CPython 3.13 and install locked dependencies
RUN uv venv /app/.venv --python python3 \
    && uv sync --frozen --no-install-project

# Copy security-corrected application sources, deployment scripts, docs, and test runner
USER 0:0
COPY --chown=qa-runner:qa-runner backend /app/backend
COPY --chown=qa-runner:qa-runner deploy /app/deploy
COPY --chown=qa-runner:qa-runner docs /app/docs
COPY --chown=qa-runner:qa-runner .github/workflows/cca_eob_linux_qa/run_tests.py /app/run_tests.py

# Switch back to unprivileged user for execution
USER qa-runner:qa-runner

# Default entrypoint executes isolated test runner
ENTRYPOINT ["/app/.venv/bin/python", "-B", "/app/run_tests.py"]
