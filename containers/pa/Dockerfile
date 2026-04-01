FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY containers/pa/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY faith/ /app/faith/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "faith.pa.app:app", "--host", "0.0.0.0", "--port", "8000"]
