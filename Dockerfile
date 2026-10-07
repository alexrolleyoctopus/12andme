FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBUG=0 \
    DATABASE_PATH=/data/db.sqlite3
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy only application files, never databases, exports or local credentials.
COPY manage.py ./
COPY config ./config
COPY budget ./budget
COPY templates ./templates
COPY static ./static

# This throwaway value is only used to collect public CSS/admin assets at build time.
RUN DEBUG=1 SECRET_KEY=build-assets-only-not-a-runtime-secret-0123456789-abcdefghij \
    python manage.py collectstatic --noinput
RUN groupadd --gid 10001 budget \
    && useradd --uid 10001 --gid budget --no-create-home budget \
    && mkdir /data && chown budget:budget /data
USER 10001:10001

# One worker keeps SQLite writes simple. No database changes occur on startup.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "2", "--timeout", "120", "--forwarded-allow-ips", ""]
