FROM python:3.13-slim

WORKDIR /app

COPY containers/tool-python/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY faith/ /app/faith/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

CMD ["python", "-c", "print('FAITH python tool scaffold')"]
