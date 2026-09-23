# Бэкенд + LibreOffice (PDF-экспорт и миниатюры)
# Контекст сборки — корень репозитория (нужны prompts/ и template_profiles/).
FROM python:3.11-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-core \
        fonts-liberation fonts-dejavu-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend/ backend/
COPY prompts/ prompts/
COPY template_profiles/ template_profiles/

WORKDIR /app/backend
ENV PYTHONUNBUFFERED=1
ENV OLLAMA_BASE_URL=http://ollama:11434

EXPOSE 8000
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]