# CUT ICT Acquisition System
FROM python:3.13-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Static files are collected at build time; WhiteNoise serves them.
RUN DJANGO_SECRET_KEY=build-only-not-secret DJANGO_ALLOWED_HOSTS=localhost \
    python manage.py collectstatic --noinput \
    && mkdir -p /app/var/uploads \
    && chown -R app:app /app/var /app/staticfiles

USER app

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--config", "deploy/gunicorn.conf.py"]
