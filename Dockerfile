# syntax=docker/dockerfile:1
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_NO_CACHE_DIR=off

WORKDIR /app

# System deps (postgres client libs for asyncpg)
RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq-dev build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install python deps
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY app ./app
COPY README.md .

EXPOSE 8000

ENV DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/bands \
    KEYCLOAK_ISSUER_URL="" \
    KEYCLOAK_AUDIENCE="" \
    AUTH_DISABLE_VERIFICATION=false

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
