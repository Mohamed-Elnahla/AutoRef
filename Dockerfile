FROM node:22-alpine AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN corepack enable && pnpm install --frozen-lockfile=false
COPY frontend/ ./
RUN pnpm build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY backend/ backend/
RUN pip install --no-cache-dir .
COPY --from=web /src/frontend/dist frontend/dist
RUN useradd --create-home --uid 10001 autoref && mkdir -p /app/data/jobs && chown -R autoref:autoref /app/data
USER autoref
EXPOSE 8000 8010
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
