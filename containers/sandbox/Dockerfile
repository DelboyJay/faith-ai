FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

CMD ["python", "-c", "import time; print('FAITH sandbox ready'); time.sleep(31536000)"]
