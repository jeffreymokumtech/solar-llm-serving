FROM python:3.12-slim
WORKDIR /app
RUN useradd -r -u 10001 gateway
COPY pyproject.toml README.md ./
COPY solarbench ./solarbench
COPY gateway ./gateway
RUN pip install --no-cache-dir ".[gateway]"
USER gateway
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s CMD python -c "import httpx;httpx.get('http://127.0.0.1:8080/healthz').raise_for_status()"
CMD ["uvicorn", "gateway.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
