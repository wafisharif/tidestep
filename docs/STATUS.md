# Status

Updated: 2026-09-07

## Done (this session — code + repo hygiene, no data refetch: no NOAA/USGS/
Overpass egress from either Claude sandbox used, same restriction the
laptop sandbox already documented)
- Repo hygiene: `cache/` untracked and gitignored (was accidentally
  committed, ~9.4 MB of regeneratable Overpass cache), `requirements.txt`
  pinned to exact versions the full test suite passed against,
  `requirements-dev.txt` added (pytest + httpx, for test_integration.py).
- `docs/LIMITATIONS.md`: Stage 10 write-up, consolidated from the
  docstring-level caveats already in the code.
- **Bug fix, found via integration testing**: `scripts/dev_seed.py` (new —
  see below) first serialized its synthetic graph with plain
  `networkx.write_graphml`; loading it through `ox.load_graphml` (what
  `api.py`/`hourly_update.py` actually do) crashed with
  `AttributeError: 'float' object has no attribute 'startswith'`.
  Production code was never affected (`streets.py` already uses
  `ox.save_graphml`/`ox.load_graphml` symmetrically) but this is exactly
  the failure class integration testing exists to catch, and the unit
  tests couldn't have caught it since they don't touch a real save/load
  round trip.
- **`scripts/dev_seed.py`** (new): a small, clearly-synthetic "Cove Harbor"
  scenario (DEM + street graph + water polygons + a 24 h tide curve) that
  exercises the entire pipeline without network. Two purposes: (1)
  integration test fixture, (2) an offline demo fallback — if live NOAA/USGS
  access is flaky right before the actual demo, `python scripts/dev_seed.py`
  gives a fully working, clearly-labelled-synthetic app to show instead of
  no app at all.
- **`tests/test_integration.py`** (new): drives the real FastAPI app (via
  `TestClient`, no separate server process) against a real local
  Postgres+PostGIS with the dev_seed scenario loaded — the first tests that
  exercise `db.py`'s actual SQL and `api.py`'s actual routing together,
  rather than mocking them. Skips itself automatically if no DATABASE_URL
  is reachable, so plain `pytest -q tests` still needs nothing running.
  Confirmed live: per-profile hazard differentiation genuinely holds
  end-to-end (adult has a route at the synthetic peak hour, vehicle_small
  and child correctly get "no safe route"), saved-route CRUD works against
  real Postgres, and the previously-silent `?hour=999` case is now a 422.
- **Predictive alerting** (`tidestep/routing.py: Router.route_window`,
  `scripts/hourly_update.py`): the alert loop previously only checked
  forecast_hour=0 ("is it flooded right now") despite its own comment
  claiming it checked the next 24 h. It now actually scans the full
  window and reports the *first* hour the saved route's usual path
  becomes unsafe, so an alert can read "floods starting around 4:00 PM
  today" instead of only firing once the flooding has already happened.
  Verified against the dev_seed scenario: child route flags unsafe
  starting hour 3, vehicle_small starting hour 1, adult never — matching
  the underlying depth data exactly. 3 new unit tests
  (`test_route_window_*` in `tests/test_routing.py`).
- **`tidestep/validate.py` + `scripts/validate_stage9.py`** (new, Stage 9):
  pulls real historical observed water levels at Kings Point for a sample
  of past days, reruns the actual hazard pipeline against them, and checks
  the model's core claim — floods when the gauge exceeds NWS minor stage,
  stays dry when it doesn't — reporting sensitivity/specificity. This
  needs NOAA network access to actually run (`python
  scripts/validate_stage9.py --days 30`, from a normal terminal); the
  logic itself has 5 passing unit tests against synthetic historical data
  (`tests/test_validate.py`) so it's proven correct and ready the moment
  it's run for real.
- `api.py` / `db.py` hardening: the `?hour=` bound was hardcoded to 48
  (inconsistent with `config.FORECAST_HOURS = 24`) — now derived from
  config. `db.unsafe_edges` now validates `profile` against the known set
  before interpolating it into a column name (defense in depth; the API
  layer already validated it, this is the actual SQL boundary).
- Test count: 10 -> **28 passing** (13 original-suite + 7 integration +
  5 validate + 3 `test_dev_seed.py`, added with the graph-path bug fix
  below).
- **Second bug fix, found by actually running the demo path end to end**
  (not just its unit tests): `scripts/dev_seed.py` wrote its synthetic
  street graph to `data/dev_seed_streets.graphml`, but `tidestep/api.py`'s
  `router()` always loads the hardcoded `streets.GRAPH_PATH`
  (`data/streets.graphml`) — a fresh clone had no file there at all
  (`/api/route` would 500), and on a machine with real fetched data already
  present, the API would route against real Long Island street coordinates
  while segments/hazard came from the synthetic scenario centered miles
  away, silently returning nonsense instead of a working demo. Fixed by
  having `dev_seed.py` write straight to `streets.GRAPH_PATH` (backing up
  any real graph already there to `.real-backup` first, never overwritten
  by a repeat run). Verified for real, not just by reasoning about it: ran
  `python scripts/dev_seed.py` against a live local Postgres, then hit
  `/api/hours`, `/api/risk?hour=7`, and `/api/route` through the actual
  `api.app` object — adult gets a routed path at the synthetic peak hour,
  `vehicle_small` correctly gets `no safe route at this hour`. `docs/NOVELTY.md`
  (new) written: cross-references TideStep's street-level/predictive/routed/
  per-profile approach against specific 2025 CAC winners (CoFIS, Watershape,
  RoadWatch, BoostT1D, VERDIS/OptiSense/Computerpreter), researched live
  rather than from memory.
- iOS app (`ios/`, new): full SwiftUI client — `Models.swift` (Codable
  structs matching `api.py`'s JSON field-for-field), `APIClient.swift`
  (async/await networking, runtime-configurable backend URL),
  `TideStepViewModel.swift` (state, per-hour risk cache, routing, autoplay,
  saved routes), `RiskMapView.swift` (iOS 17 MapKit risk polylines,
  tap-to-route via `MapReader`/`SpatialTapGesture`), `ContentView.swift`
  (map + control panel + time slider, matching `frontend/index.html`'s
  layout and hazard colors exactly), `SavedRoutesView.swift`,
  `SettingsView.swift`. Reviewed line-by-line against `api.py`'s actual
  response shapes and Apple's documented MapKit SwiftUI API, but **not
  compiled** — no Swift/Xcode toolchain in either Claude sandbox; see
  `ios/README.md` for exact Xcode setup steps and honestly-scoped known
  gaps (no offline cache, no push notifications, no CoreLocation).

## Done (earlier)
- Stage 0: scope locked (docs/SCOPE.md, tidestep/config.py). Station =
  Kings Point 8516945, bbox = Manhasset Bay, 24h hourly, still-water ponding.
- Stage 1 (code): `tidestep/coops.py` (NYOFS + predictions + datums, hourly
  max, MLLW->NAVD88), `tidestep/dem.py` (3DEP ImageServer bbox export, tiled
  + merged), `tidestep/streets.py` (osmnx road graph + OSM water features).
  `scripts/fetch_all.py` runs all of it. Unit tests in `tests/` pass.

- Stage 1 (data): water levels fetched on the laptop (24 rows, NYOFS peak
  1.59 m NAVD88 on 2026-09-07 00Z, just under NWS minor 1.77 m).
- Stages 2-3 (code): `tidestep/segments.py` (15 m segments, min DEM
  elevation + its pixel), `tidestep/floodfill.py` (seed mask from
  nodata/MLLW/OSM water, scipy.ndimage.label connectivity, bathtub kept for
  comparison), `tidestep/hazard.py` (depth, inlet factor, per-profile safe
  flags). `scripts/build_hazard.py` runs them. Synthetic-terrain tests pass,
  including the cut-off-basin case the bathtub model gets wrong.

- Stages 2-3 (data): `scripts/build_hazard.py` ran on the real DEM
  (3913 x 4214 px, 1 m) and street graph (770 nodes / 1944 edges -> 5830
  segments) in 18 s. At the 2026-09-07 00Z peak (1.59 m NAVD88) 60 segments
  flood, all waterfront footways/paths (Manhasset Bay Walk etc.), no roads
  — consistent with the level being below NWS minor stage (1.77 m).
  Seed rule changed from MLLW to `SEED_ELEVATION_M = -1.0` because 3DEP
  hydro-flattens the bay at about -1.1 m NAVD88 (see config.py).

- OFS bias check (2026-09-06, last 48 h): obs were +0.38 m above
  predictions (real non-tidal water), OFS was +0.26 m above obs. So the
  0.4-1.1 m OFS-minus-predictions gap was part weather, part model bias.
  `fetch_forecast_frame` now subtracts the trailing 48 h mean OFS-obs bias
  (columns ofs_raw_m / ofs_bias_m / ofs_navd88_m in water_levels.csv).

- Stages 4-8 (code, tested end to end against a local PostGIS in Claude's
  sandbox with the real Sept 6 data): `docker-compose.yml` + `tidestep/db.py`
  (schema, loaders, GeoJSON + unsafe-edge queries, saved routes),
  `tidestep/api.py` (FastAPI: /api/hours, /api/risk, /api/route,
  /api/routes), `tidestep/routing.py` (Dijkstra with unsafe edges removed,
  vehicle profiles snap to drivable nodes, baseline comparison),
  `frontend/index.html` (Leaflet, hour slider with play button, click-to-
  route, per-profile colouring; Leaflet vendored so the demo works offline),
  `scripts/hourly_update.py` (fetch -> hazard -> PostGIS -> saved-route
  check -> email/log alert). 10 unit tests pass.

## Bug found and fixed: study area
The first bbox left the Kings Point gauge outside and, because the two
shores of Manhasset Bay only connect by road south of the box, osmnx's
default largest-component filter silently dropped the entire west shore
(graph had nodes only between lon -73.711 and -73.700). Fixed:
`BBOX = (40.795, -73.775, 40.845, -73.695)` and `retain_all=True`.
**data/ must be regenerated**: delete `data/dem_1m.tif`,
`data/streets.graphml`, `data/water.gpkg`, `data/segments.gpkg`, then run
fetch_all -> build_hazard -> load_db again. DEM will be ~4x larger
(about 150 MB); build_hazard should take about a minute.

## Next (needs a normal terminal with NOAA/USGS/Overpass network + Docker —
not available in either Claude sandbox used this session)
1. `git pull`, then `docker compose up -d`.
2. Regenerate data with the corrected bbox: delete `data/dem_1m.tif`,
   `data/streets.graphml`, `data/water.gpkg`, `data/segments.gpkg`, then
   `python scripts/fetch_all.py && python scripts/build_hazard.py && python
   scripts/load_db.py`. This is also what finally populates `near_inlet`
   for real (it was all False last run because `water.gpkg` didn't exist
   yet).
3. `python scripts/validate_stage9.py --days 30` — the logic is tested and
   ready, this just needs to actually hit NOAA. Paste the printed
   sensitivity/specificity into the submission's technical-challenges
   answer.
4. Run the app for real (`uvicorn tidestep.api:app --reload`) and record
   demo footage: the time slider across a real flood cycle, then the
   profile switch showing the same trip flood-blind vs. flood-aware for
   adult vs. vehicle.
5. iOS app: see ios/README.md — Swift source is written and ready to open
   in Xcode; needs a Mac to build/run since neither Claude sandbox can
   compile Swift/SwiftUI.

## Notes
- `ofs_water_level` returns 6-minute data; we take the hourly max.
- CO-OPS gaps come back as empty strings; `_to_series` drops them.
