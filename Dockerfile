FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRIP_HOST=0.0.0.0 \
    TRIP_PORT=8000 \
    TRIP_DATA_DIR=/data
WORKDIR /app
RUN groupadd --gid 10001 trip && useradd --uid 10001 --gid trip --no-create-home trip
COPY server.py seed_data.json shanghai_extra.json ./
COPY ai_service.py city_catalog.py city_catalog.json ./
COPY daily_planner.py daily_plan_store.py daily_ai.py ai_planning_prompts.py ./
COPY china_regions.py china_province_cities.json ./
COPY place_cache.py project_store.py ./
COPY place_taxonomy.py place_taxonomy.json ./
COPY metro_maps.py metro_sources.json ./
COPY amap_service.py ./
COPY scenic_catalog.py scenic_aliases.py ./
COPY itinerary_export.py itinerary_links.py travel_guidance.py project_itinerary.py ./
COPY resources/scenic ./resources/scenic
COPY backup_all.py ./
COPY public ./public
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"
CMD ["python", "server.py"]
