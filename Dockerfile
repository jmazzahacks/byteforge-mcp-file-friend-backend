FROM python:3.13-slim@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39 AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc /uv /bin/uv
WORKDIR /app
COPY pyproject.toml build-requirements.txt README.md LICENSE ./
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
RUN uv pip install --system --require-hashes -r build-requirements.txt
COPY dev_scripts/build_internal_wheels.py /app/dev_scripts/build_internal_wheels.py
RUN python dev_scripts/build_internal_wheels.py --out-dir /wheels
COPY src/ src/
RUN uv build --wheel --no-build-isolation --out-dir /wheels

FROM python:3.13-slim@sha256:57b29a7c050c4e679e4bea572e14b3965cf4b5839de2da49d55e49ffc4024f39
COPY --from=ghcr.io/astral-sh/uv:0.12.22@sha256:f513a91fc62fe7c17567eee97230dd198e43edb8a9fbecca843714a4358fe1bc /uv /bin/uv
WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN uv pip install --system --require-hashes -r requirements.txt
COPY --from=builder /wheels/ /wheels/
RUN uv pip install --system --no-deps /wheels/*.whl && rm -rf /wheels \
    && useradd --uid 1000 --create-home appuser \
    && mkdir -p /data/ff && chown appuser:appuser /data/ff
COPY database/ /app/database/
COPY dev_scripts/setup_database.py /app/dev_scripts/setup_database.py
COPY entrypoint.sh gunicorn_conf.py /app/
USER appuser
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FF_DATA_DIR=/data/ff
EXPOSE 8081
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/health', timeout=2)"
CMD ["sh", "/app/entrypoint.sh"]
