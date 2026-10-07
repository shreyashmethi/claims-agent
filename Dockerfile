# Stage 1: build the console
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: API + agents, serving the built console
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt requirements-llm.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-llm.txt
COPY . .
COPY --from=web /web/dist ./frontend/dist
RUN python cli.py seed
ENV DEMO_STEP_DELAY_MS=300
EXPOSE 8000
CMD ["uvicorn", "app.api.server:server", "--host", "0.0.0.0", "--port", "8000"]
