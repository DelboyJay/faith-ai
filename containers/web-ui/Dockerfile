FROM node:22-alpine AS frontend-build

WORKDIR /app

COPY package.json package-lock.json* ./
COPY web/build-faith-ui.mjs /app/web/build-faith-ui.mjs
COPY web/js/ /app/web/js/
COPY web/src/ /app/web/src/

RUN npm ci
RUN npm run build

FROM python:3.13-slim

WORKDIR /app

COPY containers/web-ui/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ /app/src/
COPY web/ /app/web/
COPY --from=frontend-build /app/web/dist/ /app/web/dist/

ENV PYTHONPATH=/app/src
ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "faith_web.app:app", "--host", "0.0.0.0", "--port", "8080"]


