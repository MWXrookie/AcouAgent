# syntax=docker/dockerfile:1.7
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PYTHONPATH=/app

RUN useradd --create-home --uid 10001 appuser
WORKDIR /app

COPY deploy/jwave-flow-core.requirements.txt /tmp/jwave-flow-core.requirements.txt
RUN pip install --no-cache-dir --timeout 120 --retries 10 \
    -r /tmp/jwave-flow-core.requirements.txt

# The migrated workflow is the only core copied into the bridge image. There
# is no external build context and no path into another checkout.
COPY cores/__init__.py /app/cores/__init__.py
COPY cores/jwave_flow/__init__.py /app/cores/jwave_flow/__init__.py
COPY cores/jwave_flow/workflow /app/cores/jwave_flow/workflow
COPY cores/jwave_flow/bridge /app/cores/jwave_flow/bridge
COPY cores/jwave_flow/kb /app/cores/jwave_flow/kb

ENV JWAVE_FLOW_BRIDGE_HOST=0.0.0.0 \
    JWAVE_FLOW_BRIDGE_PORT=8765 \
    JWAVE_FLOW_KB_PATH=/app/cores/jwave_flow/kb/jwave_kb_organized.md \
    JWAVE_FLOW_EXECUTOR_URL=http://jwave-executor:8010

USER 10001:10001
EXPOSE 8765
HEALTHCHECK --interval=10s --timeout=3s --retries=6 \
    CMD python -c "import urllib.request; \
urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=2).read()"
CMD ["python", "-m", "cores.jwave_flow.bridge.server"]
