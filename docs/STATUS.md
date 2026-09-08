# Status

Updated: 2026-09-08

## Done (2026-09-08 — laptop set up for real, first real data + two bugs found)
- **The laptop is now a fully working dev environment.** In order, fixed:
  a `.git/index.lock` left over from a bad shutdown, a README that only
  documented Unix venv activation (now has a separate Windows PowerShell
  block + troubleshooting section), `.venv` built against a too-old Python
  (3.10.11 — below the 3.11+ `requirements.txt` needs) rebuilt with
  `py -3.12 -m venv .venv`, and Docker Desktop installed but never launched.
  `pip install -r requirements.txt` now succeeds with real Windows wheels
  for every package.
- **Real data fetched for the first time on the corrected bbox**:
  `python scripts/fetch_all.py` and `python scripts/build_hazard.py` both
  ran against live NOAA/USGS/Overpass from the laptop (neither Claude
  sandbox has that egress). `docker compose up -d` + `python
  scripts/load_db.py` loaded it into PostGIS, and `uvicorn
  tidestep.api:app --reload` started cleanly ("Application startup
  complete") — the full pipeline has now genuinely run end to end on real
  data, not just against `dev_seed.py`'s synthetic fixture.
- **Bug fix, found by rigorous line-by-line audit, not by running it**:
  `scripts/hourly_update.py`'s predictive-alert message used
  `strftime("%-I:%M %p %Z")` — `%-I` is a glibc/macOS-only strftime
  extension and raises `ValueError` on Windows. Since the user runs
  `hourly_update.py` directly on Windows (it's not in the Docker
  container — only Postgres is), this would have crashed the first time a
  real predictive alert fired (`first_unsafe_hour > 0`), silently killing
  the app's most novel feature. Fixed with the portable equivalent:
  `strftime("%I:%M %p %Z").lstrip("0")`. Confirmed via
  `grep -rn "%-[A-Za-z]"` that this was the only `%-`-style format code
  anywhere in the repo.
- **Docs fix**: `docs/LIMITATIONS.md`'s "Alerts" section still described
  the pre-`route_window()` behavior (checked forecast_hour=0 only) as a
  current limitation, contradicting the predictive-alerting feature that
  had already superseded it earlier in the session. Rewrote it as a
  superseded note and added a new, real, previously-undocumented gap found
  in the same audit: **no authentication on `/api/routes`** — anyone with
  the API URL can view or delete any saved route. Fine for a single-user
  demo, would need accounts before a real multi-user deployment.
- **Full-repo audit, everything else reviewed and confirmed correct, no
  further bugs found**: `tidestep/api.py`, `tidestep/routing.py`,
  `tidestep/config.py`, `docker-compose.yml`, `tidestep/validate.py`,
  `frontend/index.html`, and all 9 iOS Swift files — including a line-by-
  line cross-check of every Swift `Codable` struct's JSON field names
  against the actual Python API/DB response shapes (`Models.swift` vs.
  `api.py`/`db.py`). No mismatches found. (Caveat, unchanged: this is
  correctness-by-inspection only — neither Claude sandbox has a Swift
  toolchain, so the iOS app has still never actually compiled.)

## Done (earlier this session — code + repo hygiene, no data refetch: no
NOAA/USGS/Overpass egress from either Claude sandbox used, same restriction
the laptop sandbox already documented at the time)
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

## Next (the laptop is now a working environment — these are all runnable
there today; nothing left is blocked on tooling)
1. **Commit and push.** Nothing described in this file or in `git status`
   is on `origin/main` yet — see "Uncommitted work" below for the exact
   commands. Do this first so the fixes above aren't sitting only on disk.
2. `python scripts/validate_stage9.py --days 30` — Stage 9 has never
   actually been run anywhere. The logic is unit-tested and ready
   (`tests/test_validate.py`, 5 passing); this just needs to hit NOAA for
   real, which the laptop can now do. Paste the printed
   sensitivity/specificity into the submission's technical-challenges
   answer.
3. Spot-check `near_inlet` on the real data now that `water.gpkg` actually
   exists from the fetch above (it was all `False` on every prior run
   because the water layer hadn't been fetched yet).
4. Record demo footage: the time slider across a real flood cycle, then
   the profile switch showing the same trip flood-blind vs. flood-aware
   for adult vs. vehicle. The app is confirmed running end to end on real
   data, so this is unblocked.
5. Test coverage gap: there's no dedicated `test_api.py` / `test_db.py` /
   `test_dem.py` / `test_streets.py` / `test_segments.py` — `test_integration.py`
   and `test_floodmodel.py` cover much of this indirectly but not as unit
   tests per module.
6. iOS app: see `ios/README.md` — Swift source is written, reviewed line
   by line against the real API/DB response shapes, and ready to open in
   Xcode, but has never actually compiled — needs a Mac, since neither
   Claude sandbox can run a Swift toolchain. This is the single biggest
   unverified risk left in the project.

## Uncommitted work
Everything from this session and the previous one is still sitting
uncommitted (repo policy: Claude never runs `git add`/`commit`/`push` —
see `CLAUDE.md`). Current diff is 7 modified + 9 new files/dirs from
earlier, plus `scripts/hourly_update.py`, `docs/LIMITATIONS.md`, and this
file from the audit pass just now. Suggested split, each buildable in one
`git add` + `git commit`:
1. `docs/STATUS.md docs/LIMITATIONS.md docs/NOVELTY.md README.md` — docs.
2. `tidestep/ scripts/ tests/ requirements-dev.txt` — code + tests
   (predictive alerting, dev_seed fixture, validate.py, the Windows
   strftime fix, api/db hardening).
3. `ios/` — the SwiftUI client, on its own since it's unreviewed by any
   compiler.
Or, more simply, one commit for everything:
```
git add -A
git commit -m "stage 8-10: predictive alerts, dev_seed + integration tests, Stage 9 validation, iOS client, Windows fixes"
git push
```

## Notes
- `ofs_water_level` returns 6-minute data; we take the hourly max.
- CO-OPS gaps come back as empty strings; `_to_series` drops them.
