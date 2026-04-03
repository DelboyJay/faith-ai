FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY containers/pa/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/src/
COPY web/ /app/web/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/health', timeout=3).read()"

CMD ["uvicorn", "faith_pa.pa.app:app", "--host", "0.0.0.0", "--port", "8000"]


