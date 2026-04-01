FROM python:3.13-slim

WORKDIR /app

COPY containers/web-ui/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY faith/ /app/faith/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "faith.web.app:app", "--host", "0.0.0.0", "--port", "8080"]
