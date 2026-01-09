FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["bash", "-lc", "adk api_server --host 0.0.0.0 --port ${PORT:-8000} /app/upsell_ranker_full/v0.0.1"]
