FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app \
    DEPLOYMENT_MODE=hosted \
    DB_BACKEND=rds_postgresql \
    INFERENCE_PROVIDER=nebius \
    AWS_REGION=us-east-1 \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r /app/requirements.lock
RUN useradd --create-home --uid 10001 appuser

COPY --chown=appuser:appuser agents /app/agents
COPY --chown=appuser:appuser evaluation /app/evaluation
COPY --chown=appuser:appuser frontend /app/frontend
COPY --chown=appuser:appuser data /app/data
COPY --chown=appuser:appuser certs/aws-rds-global.pem /app/certs/aws-rds-global.pem
COPY --chown=appuser:appuser .streamlit/config.toml /app/.streamlit/config.toml
COPY --chown=appuser:appuser README.md LICENSE /app/

USER appuser
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).read()"
CMD ["python", "-m", "streamlit", "run", "frontend/app.py", "--server.address=0.0.0.0", "--server.port=8501"]
