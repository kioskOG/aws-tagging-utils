# ─── Build Stage ─────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src/ src/
COPY mcp_server.py .

# ─── Runtime Stage ───────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Security: run as non-root
RUN groupadd --gid 1001 appuser \
    && useradd --uid 1001 --gid 1001 --create-home appuser

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --from=builder /app/src/ src/
COPY --from=builder /app/mcp_server.py .

# Ensure src is importable
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED="1"
ENV LOG_FORMAT="json"
ENV LOG_LEVEL="INFO"

USER appuser

# Health check (validates import chain works)
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "from src.config import DEFAULT_REGION; print('ok')" || exit 1

# Default: run MCP server
ENTRYPOINT ["python", "mcp_server.py"]
