FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# The container clock is UTC; every calendar calculation runs in Europe/Berlin
# via zoneinfo, which needs the tz database present.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY fonts ./fonts

EXPOSE 8000

# /healthz sits outside URL_PREFIX on purpose — the platform cannot be given
# the secret path. Python rather than curl because the slim image ships
# neither curl nor wget, and a healthcheck that cannot run marks every
# deployment unhealthy. The generous start period covers the first calendar
# fetch at startup.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/healthz', timeout=4)"

# PORT is optional; the default matches EXPOSE and keeps `docker run` usable.
CMD ["sh", "-c", "uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
