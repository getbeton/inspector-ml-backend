FROM python:3.11-slim

WORKDIR /app

# Install Python deps
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

ENV PYTHONPATH=/app/upsale-agent
ENV PYTHONUNBUFFERED=1

# Run ADK API server:
# - bind 0.0.0.0 (Railway needs this) :contentReference[oaicite:2]{index=2}
# - use Railway's $PORT
# - pass AGENTS_DIR where each subdir is an agent app :contentReference[oaicite:3]{index=3}
CMD ["bash", "-lc", "set -euxo pipefail; pwd; ls -la /app; find /app -maxdepth 4 -type d -name 'upsell_agent' -o -name 'upsale_ranker_full' -o -name 'v0.0.1' || true; adk api_server --host 0.0.0.0 --port ${PORT:-8000} /app/upsale-agent/upsale_ranker_full/v0.0.1"]

