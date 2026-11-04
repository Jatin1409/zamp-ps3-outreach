# --- build the React frontend -------------------------------------------------
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build   # outputs to ../backend/static

# --- python API + worker ------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt
COPY backend/ backend/
COPY --from=web /backend/static backend/static
WORKDIR /app/backend
EXPOSE 10000
# One process: FastAPI serves the API + SSE + built frontend, and runs the background job worker.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-10000} --timeout-keep-alive 75 --proxy-headers --forwarded-allow-ips='*'"]
