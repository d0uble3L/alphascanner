FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ALPHASCANNER_DB_PATH=/data/alphascanner.db

WORKDIR /app

COPY pyproject.toml ./
COPY alphascanner ./alphascanner
RUN pip install .

RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "alphascanner.api:app", "--host", "0.0.0.0", "--port", "8000"]
