FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /opt/mirai

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir .

VOLUME ["/var/lib/mirai"]
EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import ssl, urllib.request; context = ssl.create_default_context(); context.check_hostname = False; context.verify_mode = ssl.CERT_NONE; urllib.request.urlopen('https://127.0.0.1:8080/v1/health', timeout=2, context=context)"

CMD ["mirai", "agent", "start", "--host", "0.0.0.0", "--port", "8080", "--data-dir", "/var/lib/mirai"]
