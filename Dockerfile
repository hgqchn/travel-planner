FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRIP_HOST=0.0.0.0 \
    TRIP_PORT=8000 \
    TRIP_DATA_DIR=/data
WORKDIR /app
RUN groupadd --gid 10001 trip && useradd --uid 10001 --gid trip --no-create-home trip
COPY server.py seed_data.json shanghai_extra.json ./
COPY public ./public
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["python", "server.py"]
