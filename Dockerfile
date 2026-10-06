# ─── Build Stage ─────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build dependencies
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir PyYAML

# Copy source
COPY src/ src/
COPY web/ web/
COPY config/ config/
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
COPY --from=builder /app/web/ web/
COPY --from=builder /app/config/ config/
COPY --from=builder /app/mcp_server.py .

# Ensure src is importable
ENV PYTHONPATH="/app"
ENV PYTHONUNBUFFERED="1"
ENV LOG_FORMAT="json"
ENV LOG_LEVEL="INFO"

# Ensure data directory exists and is writable for SQLite persistence
RUN mkdir -p /app/data && chown appuser:appuser /app/data
ENV SQLITE_DB_PATH="/app/data/app.db"

USER appuser

EXPOSE 5050

# Health check hits the /health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5050/health', timeout=3)" || exit 1

# Default: run Flask Web API (production mode, no debug, bound to all interfaces)
CMD ["python", "-m", "flask", "--app", "web.app", "run", "--host=0.0.0.0", "--port=5050", "--no-debugger", "--no-reload"]
