# TideStep

24-hour street-level tidal flooding forecast and safe-routing app for the
North Shore of Long Island (NY-03). Built for the 2026 Congressional App
Challenge.

- Plan: [docs/PIPELINE.md](docs/PIPELINE.md)
- Locked scope decisions: [docs/SCOPE.md](docs/SCOPE.md)
- Progress: [docs/STATUS.md](docs/STATUS.md)
- Known limitations (Stage 10): [docs/LIMITATIONS.md](docs/LIMITATIONS.md)
- Why this beats the existing flood/tide apps: [docs/NOVELTY.md](docs/NOVELTY.md)
- iOS app: [ios/README.md](ios/README.md)

## Run it

Needs Python 3.11+ (64-bit) and [Docker Desktop](https://www.docker.com/products/docker-desktop/)
(for the PostGIS container — Docker Desktop must be **installed and running**,
not just installed, before `docker compose up -d` will work).

**macOS / Linux:**

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

**Windows (PowerShell):** the same 9 steps, run one at a time (don't paste
them all in as one block — a multi-line paste can make PowerShell treat it
as a single incomplete command and silently skip steps):

```
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
docker compose up -d
python scripts\fetch_all.py
python scripts\build_hazard.py
python scripts\load_db.py
uvicorn tidestep.api:app --reload
python scripts\hourly_update.py
```

If `Activate.ps1` is blocked by execution policy, either run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first, or use
`.venv\Scripts\activate.bat` from `cmd.exe` instead. **Every new terminal
window needs the activate line again** — `pip install` and `python` only
see `requirements.txt`'s packages while `.venv` is activated in that shell.

**Troubleshooting:**

- `ModuleNotFoundError: No module named 'geopandas'` (or any other package)
  means step 0's `pip install -r requirements.txt` hasn't actually run in the
  Python you're using — most often because `.venv` wasn't activated in the
  current terminal, or the install step got skipped in a multi-line paste
  (see above). Activate `.venv` and rerun `pip install -r requirements.txt`
  on its own.
- `docker : The term 'docker' is not recognized` means Docker Desktop isn't
  installed. Install it from the link above, launch it once (it needs to
  show as running in the system tray / menu bar), then retry
  `docker compose up -d`. No admin-installed Docker Desktop, no PostGIS —
  everything downstream of step 0 needs it.

Tests: `pip install -r requirements-dev.txt && pytest -q tests`. Most of the
suite needs no network or database; `tests/test_integration.py` needs a
reachable Postgres (the docker-compose one, or `DATABASE_URL` pointed
elsewhere) and skips itself automatically if none is running.

### No internet / demo fallback

If NOAA, USGS, or Overpass access is flaky (or you just want to see the app
without waiting on real downloads), `python scripts/dev_seed.py` builds a
small, clearly-synthetic "Cove Harbor" scenario — DEM, streets, water
polygons, a full 24 h tide curve — and loads it straight into PostGIS. Then
`uvicorn tidestep.api:app --reload` works exactly like it does on real
data, no manual steps: `dev_seed.py` writes its graph to `data/streets.graphml`
itself (the same path `api.py` always loads), backing up any real graph
already there to `data/streets.graphml.real-backup` first so a real fetch
is never lost — restore it afterward with
`mv data/streets.graphml.real-backup data/streets.graphml`.

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
tidestep/routing.py    networkx flood-avoiding Dijkstra + route_window (6, 8)
frontend/index.html    Leaflet map + time slider + route form (7)
scripts/hourly_update.py  operational loop + predictive alerts (8)
tidestep/validate.py   Stage 9: retroactive check against real NOAA history
scripts/validate_stage9.py  CLI for the above
scripts/dev_seed.py     synthetic fixture: offline demo + integration tests
tests/test_integration.py  real FastAPI + real PostGIS, no mocks
ios/                    SwiftUI client (MapKit risk map, routing, alerts)
```
