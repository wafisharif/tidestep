# TideStep

24-hour street-level tidal flooding forecast and safe-routing app for the
North Shore of Long Island (NY-03). Built for the 2026 Congressional App
Challenge.

- Plan: [docs/PIPELINE.md](docs/PIPELINE.md)
- Locked scope decisions: [docs/SCOPE.md](docs/SCOPE.md)
- Progress: [docs/STATUS.md](docs/STATUS.md)

## Run it

```
# 0. one-time setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
docker compose up -d                 # PostGIS on localhost:5432

# 1. data (needs internet; ~2 min first time, DEM is 37 MB)
python scripts/fetch_all.py

# 2-3. flood model -> data/segments.gpkg, data/hazard.csv
python scripts/build_hazard.py

# 4. load PostGIS
python scripts/load_db.py

# 5-7. API + map at http://127.0.0.1:8000
uvicorn tidestep.api:app --reload

# 8. hourly refresh + alerts (also what cron runs)
python scripts/hourly_update.py
```

Tests: `pytest -q tests` (no network or database needed).

## Layout

```
tidestep/config.py     station, bbox, datums, thresholds (Stage 0)
tidestep/coops.py      NOAA CO-OPS client, datum conversion, OFS bias (1)
tidestep/dem.py        USGS 3DEP DEM download (1)
tidestep/streets.py    OSM street graph + water features (1)
tidestep/segments.py   15 m road segments, min elevation per segment (2)
tidestep/floodfill.py  connected flood-fill (2)
tidestep/hazard.py     depth + child/adult/vehicle safety flags (3)
tidestep/db.py         PostGIS schema, loaders, queries (4)
tidestep/api.py        FastAPI endpoints (5)
tidestep/routing.py    networkx flood-avoiding Dijkstra (6)
frontend/index.html    Leaflet map + time slider + route form (7)
scripts/hourly_update.py  operational loop + alerts (8)
```
