# HKEx filings — live MCP gateway over HTTP.
#
# The node runs this as a WSGI app (`api.mcp:app`) behind gunicorn on 8041; the
# repository's other Dockerfile is the stdio MCP for Glama sandboxes and is
# unrelated. Standard library plus the `mcp` extra, so the image stays small.
#
#   docker build -f docker/http.Dockerfile -t hkex-mcp:<stamp> .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SETUPTOOLS_SCM_PRETEND_VERSION=2.4.0

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY api ./api

RUN pip install ".[mcp]" gunicorn

# A fixed non-root uid; nothing in the app writes to disk.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app
USER 10001:10001

EXPOSE 8041

CMD ["gunicorn", "--bind", "0.0.0.0:8041", "--workers", "2", \
     "--access-logfile", "-", "--error-logfile", "-", "api.mcp:app"]
