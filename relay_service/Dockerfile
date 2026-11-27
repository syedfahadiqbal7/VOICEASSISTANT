FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements-relay.txt ./requirements-relay.txt
RUN python -m pip install --no-cache-dir -r requirements-relay.txt \
    && useradd --system --uid 10001 --create-home relay
COPY relay_service ./relay_service
USER 10001:10001

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1

# Trust forwarded client addresses only from the fixed Caddy address on the
# private relay network. Never use "*" for an internet-facing WebSocket service.
CMD ["uvicorn", "relay_service.server:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "172.30.240.2", "--ws-max-size", "65536", "--no-access-log"]
