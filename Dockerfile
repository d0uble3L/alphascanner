FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ALPHASCANNER_DB_PATH=/data/alphascanner.db

RUN apt-get update && \
    apt-get upgrade -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY alphascanner ./alphascanner
RUN pip install .

RUN mkdir -p /data && \
    useradd -m -u 1000 appuser && \
    chown appuser /data

VOLUME ["/data"]
EXPOSE 8000

USER appuser

CMD ["uvicorn", "alphascanner.api:app", "--host", "0.0.0.0", "--port", "8000"]
