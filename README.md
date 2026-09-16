# TideStep

36-hour street-level tidal flooding forecast and safe-routing web app for
the North Shore of Long Island (NY-03). Built for the 2026 Congressional App
Challenge.

What it does: pulls NOAA's NYOFS water-level guidance for the Kings Point
gauge, runs a hydrologically connected flood-fill over a 1 m USGS LiDAR DEM,
and tells you which 15 m street segments around Manhasset Bay will pond at
each hour, how deep, and whether they are safe for a child, an adult, a
wheelchair user, or a car. It routes around unsafe segments, plans the best
departure hour, finds the nearest dry shelter, emails you if a saved route
is about to flood, shows the same map under +30/+60/+100 cm of sea-level
rise, and can replay any past day from the gauge's recorded levels.

- Plan: [docs/PIPELINE.md](docs/PIPELINE.md)
- Locked scope decisions: [docs/SCOPE.md](docs/SCOPE.md)
- Progress and next steps: [docs/STATUS.md](docs/STATUS.md)
  (full pass-by-pass history in [docs/CHANGELOG.md](docs/CHANGELOG.md))
- Validation against documented street flooding: [docs/VALIDATION.md](docs/VALIDATION.md)
- Known limitations (Stage 10): [docs/LIMITATIONS.md](docs/LIMITATIONS.md)
- Why this beats the existing flood/tide apps: [docs/NOVELTY.md](docs/NOVELTY.md)
- iOS client (uncompiled draft): [ios/README.md](ios/README.md)

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

# 1. data (needs internet; ~5 min first time, DEM is 60 MB)
python scripts/fetch_all.py

# 2-3. flood model, 4 sea-level scenarios -> data/segments.gpkg, data/hazard.csv (~3 min)
python scripts/build_hazard.py

# 4. load PostGIS (~2 min)
python scripts/load_db.py

# 5-7. API + map at http://127.0.0.1:8000
uvicorn tidestep.api:app --reload

# 8. hourly refresh + alerts (also what cron runs)
python scripts/hourly_update.py

# 9. validation against documented flooded streets -> docs/VALIDATION.md
python scripts/validate_streets.py

# optional: precompute a historical replay so the app serves it instantly
python scripts/replay_day.py 2022-12-23
```

**Everything in Docker** (after step 1-3 have produced `data/` on the host):
`docker compose up -d` starts PostGIS *and* the API on http://localhost:8000;
`docker compose run --rm api python scripts/load_db.py` loads the data. The
image is built from the `Dockerfile` in this directory. Copy `.env.example`
to `.env` for SMTP alert settings.

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
tidestep/hazard.py     depth + child/adult/wheelchair/vehicle safety flags,
                        sea-level-rise scenarios (3)
tidestep/db.py         PostGIS schema, loaders, queries (4)
tidestep/api.py        FastAPI endpoints (5)
tidestep/routing.py    networkx flood-avoiding Dijkstra + route_window (6, 8)
tidestep/shelters.py   real shelter buildings (school/hospital/fire/police/
                        community center) from OSM, for route_to_safety (11)
tidestep/resilience.py network-wide chokepoint (single point of failure)
                        analysis, ranked by forecast flood exposure
tidestep/replay.py     historical replay from observed gauge levels
tidestep/nws.py        live NWS alert feed (Coastal Flood Advisory/Warning)
frontend/index.html    Leaflet map + time slider + route form, phone layout (7)
scripts/hourly_update.py  operational loop + predictive alerts (8)
tidestep/validate.py   Stage 9: retroactive check against real NOAA history
scripts/validate_stage9.py  CLI for the above (gauge-level check)
scripts/validate_streets.py Stage 9b: hit/miss against streets documented as
                        flooded by NWS/press -> docs/VALIDATION.md
scripts/replay_day.py   precompute a past day's replay
.github/workflows/ci.yml  runs the test suite (with PostGIS) on every push
scripts/check_ofs_bias.py  diagnostic: OFS/predictions vs. observed water
                        level over the last 48h, to catch a datum mismatch
                        before trusting a forecast
scripts/dev_seed.py     synthetic fixture: offline demo + integration tests
tests/test_integration.py  real FastAPI + real PostGIS, no mocks
ios/                    SwiftUI client (MapKit risk map, routing, alerts)
```
