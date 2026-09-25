# Бэкенд + LibreOffice (PDF-экспорт и миниатюры)
# Контекст сборки — корень репозитория (нужны prompts/ и template_profiles/).
FROM python:3.11-slim-bookworm

# Шрифты: метрически совместимые замены + попытка поставить фирменный Play.
# Без них LibreOffice подставляет DejaVu, и PDF/миниатюры (а значит и VLM-аудит)
# показывают не ту типографику, что лежит в PPTX.
ARG INSTALL_BRAND_FONTS=1
# ВАЖНО: нужен именно libreoffice-impress — без него Writer не открывает PPTX
# («source file could not be loaded»), и PDF/миниатюры молча не работают.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-impress libreoffice-writer libreoffice-core \
        fonts-liberation fonts-dejavu-core fonts-noto-core \
        fonts-crosextra-carlito fonts-crosextra-caladea \
        unzip curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Play — открытая гарнитура VK (SIL OFL). Ставим, если сеть доступна: сборка не
# должна падать в закрытом контуре, поэтому ошибки загрузки игнорируются.
RUN if [ "$INSTALL_BRAND_FONTS" = "1" ]; then \
      mkdir -p /usr/share/fonts/truetype/play && \
      for file in Play-Regular Play-Bold; do \
        curl -fsSL -o "/usr/share/fonts/truetype/play/$file.ttf" \
          "https://raw.githubusercontent.com/google/fonts/main/ofl/play/$file.ttf" || true; \
      done; \
      find /usr/share/fonts/truetype/play -name '*.ttf' -size -1k -delete; \
    fi

RUN fc-cache -f >/dev/null 2>&1 || true

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