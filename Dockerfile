FROM python:3.12-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ALPHASCANNER_DB_PATH=/data/alphascanner.db

RUN apk update && \
    apk upgrade --no-cache

WORKDIR /app

COPY pyproject.toml requirements-lock.txt ./
COPY alphascanner ./alphascanner
RUN pip install --no-deps -r requirements-lock.txt && \
    pip install --no-deps .

RUN mkdir -p /data && \
    addgroup -g 1000 appuser && \
    adduser -D -u 1000 -G appuser appuser && \
    chown appuser /data

VOLUME ["/data"]
EXPOSE 8000

USER appuser

CMD ["uvicorn", "alphascanner.api:app", "--host", "0.0.0.0", "--port", "8000"]
