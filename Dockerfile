FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/data && chown appuser:appuser /app/data
WORKDIR /app

COPY requirements.lock.txt /app/requirements.lock.txt
RUN pip install --no-cache-dir --timeout 120 --retries 10 \
    -r /app/requirements.lock.txt

COPY app/ /app/app/
# Reviewed source identity only; the gateway never launches the executor here.
COPY executor/executor.py /app/executor/executor.py
# Server-owned fixed VAL-1 recipe/validator; no caller-selected code assets.
COPY scripts/validation/ /app/scripts/validation/
# Explicit operator preparation/status/reuse CLI; learning remains opt-in.
COPY scripts/skill_library/ /app/scripts/skill_library/
COPY docs/ /app/docs/
ARG APP_MODULE=app.server_safe
ENV APP_MODULE=${APP_MODULE}
ARG INSTALL_DEV=false
COPY requirements.dev.lock.txt /app/requirements.dev.lock.txt
RUN if [ "$INSTALL_DEV" = "true" ]; then pip install --no-cache-dir -r /app/requirements.dev.lock.txt; fi
USER 10001:10001
CMD ["/bin/sh", "-c", "exec python -m ${APP_MODULE}"]
