FROM python:3.13-slim

WORKDIR /app

COPY containers/agent-base/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/src/
COPY web/ /app/web/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

CMD ["python", "-c", "print('FAITH agent base container scaffold')"]


