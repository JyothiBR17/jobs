FROM node:22-alpine AS web
WORKDIR /project/web
COPY web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 jobhunter \
    && mkdir -p /srv/data \
    && chown -R 10001:10001 /srv/data
COPY app app
COPY migrations migrations
COPY --from=web /project/app/static app/static
USER 10001
EXPOSE 8000
CMD ["uvicorn","app.api:app","--host","0.0.0.0","--port","8000"]
