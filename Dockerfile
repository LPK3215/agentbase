# ====================================================================== #
# Multi-stage Dockerfile for agentbase                                   #
# ====================================================================== #
# Stage 1: Builder — install dependencies into a virtualenv               #
# ====================================================================== #
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build-time system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy only dependency manifest first (better layer caching)
COPY pyproject.toml ./
COPY src/ src/
COPY README.md ./

# Install into a virtualenv for clean copy to runtime stage
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install the package with postgres and api extras
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir ".[postgres,api]"

# ====================================================================== #
# Stage 2: Runtime — minimal image with only what's needed to run       #
# ====================================================================== #
FROM python:3.11-slim AS runtime

# Install runtime system dependencies (no gcc/g++)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy the virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy application files (configs, workspace, etc.)
COPY pyproject.toml ./
COPY src/ src/
COPY configs/ configs/
COPY workspace/ workspace/

# Create data directory for SQLite (if used)
RUN mkdir -p data

# Expose API port
EXPOSE 8000

# Health check — uses curl for a more robust check
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

# Run as non-root user for security
RUN useradd -m -u 1000 agentbase && chown -R agentbase:agentbase /app
USER agentbase

# Start the API server
CMD ["uvicorn", "agentbase.api:app", "--host", "0.0.0.0", "--port", "8000"]
