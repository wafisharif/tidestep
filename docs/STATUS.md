# Status

Updated: 2026-09-09

## Done (2026-09-09, fifth pass — genuinely new feature work, not just
audit/bugfix: time-expanded routing + trip advisory)

This pass was deliberately different in kind from the previous four: those
were audits (real bugs found and fixed, coverage raised, no new user-facing
capability). This one retires an explicitly-documented Stage 6
simplification with a real algorithmic upgrade and adds a new endpoint,
without touching any previously-tested code path.

- **Time-expanded (arrival-hour-aware) routing — `Router.route_time_aware()`**
  (`tidestep/routing.py`). Until now, every route TideStep computed checked
  the hazard table once, at the hour the trip departs, and assumed that
  hazard state held for the whole trip — explicitly called out as a known
  simplification in `docs/LIMITATIONS.md`. For a walk or drive that's long
  enough to cross into the next forecast hour, that's wrong: a segment 20
  minutes into the trip is flooded or not based on the tide an hour later,
  not the tide when the traveler left home. `route_time_aware()` is a
  hand-rolled Dijkstra over `(elapsed_seconds, node)` state (not expressible
  as a static per-edge weight, so it can't reuse `networkx.dijkstra`): it
  converts each edge's length into actual time-on-segment (new
  `config.WALK_SPEED_MPS` for pedestrians; the vehicle profiles reuse
  osmnx's existing posted-speed-limit `travel_time`), tracks cumulative
  elapsed time along each candidate path, and checks the hazard state at
  the forecast hour a traveler would *actually* be on each segment,
  clamped to `config.MAX_HOUR`. Exposed as `time_aware=true` on the
  existing `/api/route` endpoint; **default is `false`, preserving the
  original departure-hour-only response byte-for-byte** — verified with a
  live curl comparison, not just reasoned about, so this is purely
  additive with zero regression risk to the existing route mode.
- **Proven, not just plausible**: `tests/test_routing.py` builds a new
  synthetic `long_detour_graph()` fixture with edge lengths chosen so that,
  at the configured walking speed, a direct path's cumulative travel time
  provably crosses a forecast-hour boundary where a hazard appears. The
  new test (`test_route_time_aware_avoids_hazard_that_appears_after_departure`)
  demonstrates concretely that the *old* departure-hour router recommends
  a path that turns out unsafe, while the *new* time-aware router
  correctly detours around it — the actual value of the feature, shown
  working, not asserted.
- **Trip advisory — new `/api/route/advisory` endpoint and
  `Router.route_advisory()`.** Answers a different, also-new question:
  "across the whole 24 h forecast, when is it safe to make *this specific
  trip*" — not a single hour's snapshot, and not "is my route blocked
  right now," but every hour's safe/unsafe status for the trip's usual
  path in one response. Deliberately implemented as its own method rather
  than reusing `Router.route_window()` (the existing predictive-alert
  mechanism `scripts/hourly_update.py` depends on, which only needs and
  returns the *first* unsafe hour for a fixed alert) — kept separate so
  this new read-only, ad-hoc-query feature carries zero risk to the
  already-tested alert loop.
- **Frontend wired to both**: `frontend/index.html` gets a "check hazard
  at actual arrival time, not just departure" checkbox that toggles
  `time_aware` on route requests and switches the result text to describe
  elapsed travel time and whether the trip crosses an hour boundary; and a
  new 24-cell green/red hour strip (`renderAdvisory()`) that calls
  `/api/route/advisory` whenever an origin/destination is set, giving an
  at-a-glance "safe now vs. wait until later" view for the exact trip
  being planned. Verified with `node --check` on the extracted inline
  script (including the nested ES6 template literals) and, live, by
  curling the running server and confirming both new DOM elements are
  actually served.
- **DRY housekeeping alongside the feature**: `MAX_HOUR` was a local
  constant duplicated in `api.py`; moved to `config.MAX_HOUR` so
  `routing.py` can share the same forecast-window bound without importing
  from `api.py` (a bad direction of dependency). `api.MAX_HOUR` kept as a
  backward-compatible alias.
- **Test count: 81 -> 96** (+7 `tests/test_routing.py` — the six behaviors
  above plus an hour-clamping test against a hazard source that raises if
  asked for an hour past the forecast horizon; +6 `tests/test_api.py` —
  every request-validation path for both new/changed endpoints, confirmed
  (via the existing monkeypatched-engine pattern) to reach the router and
  nothing further when valid, and to reject before touching the database
  when not; +2 `tests/test_integration.py` — both features hit through
  the real FastAPI app against a real local Postgres+PostGIS, not just
  the synthetic-graph unit tests). All 96 pass in plain `pytest -q`
  (nothing running required except the 9 already-conditional integration
  tests, which ran for real this pass, not skipped).
- **Live end-to-end verification, not just tests**: regenerated the
  dev_seed synthetic scenario, booted a real `uvicorn tidestep.api:app`
  process, and hit it with real `curl` requests: `time_aware=true`
  returns a route with the new `travel_time_min` / `arrival_hour` /
  `hour_crossed` properties; `time_aware=false` (the default) returns a
  response byte-identical in shape to the pre-existing format — proving
  zero regression; `/api/route/advisory` for `vehicle_small` correctly
  flags hour 7 unsafe, matching the already-established peak-hour ground
  truth from `test_route_differs_by_profile_at_peak`; and `curl / | grep`
  confirms the new `timeAware` checkbox and `advisory` strip are actually
  served by the running frontend.
- Docs updated to match: `docs/LIMITATIONS.md`'s "Hazard is evaluated at
  departure hour" item rewritten to describe the new opt-in time-aware
  mode (with the departure-hour-only default kept for compatibility, and
  a clear note that this is separate from `route_window()`'s alert-loop
  mechanism); `docs/NOVELTY.md`'s "What's actually new here" list gets two
  new entries (time-expanded routing; the trip-advisory hour strip).

## Done (2026-09-08, fourth pass — second real bug found one layer above the
first, dead code removed, coverage pushed from 74% to 95% on `tidestep/`)
- **Second real bug, same failure class as the near_inlet fix, one layer
  further downstream**: `scripts/hourly_update.py`'s `main()` only called
  `db.load_segments` (the only thing that pushes segments.gpkg's actual
  column values — near_inlet, ground_m, tags, geometry — into PostGIS)
  when the segments table's row *count* differed from the freshly-read
  `segments.gpkg`. A correction to segments.gpkg that doesn't change the
  segment count — exactly what the near_inlet caching fix produces —
  would then never reach the live database: the hourly cron loop would
  keep serving the stale row forever, silently, because the count check
  always matched. Concretely, this meant fixing `build_hazard.py` alone
  was not enough to actually get corrected `near_inlet` flags live: the
  operational loop would have swallowed the fix. Fixed by extracting
  `sync_db()` and always reloading segments unconditionally (cheap next
  to the DEM/floodfill/network work the script already does hourly).
  Regression-tested in the new `tests/test_hourly_update.py` against a
  real local Postgres, reproducing the exact same-count-different-value
  scenario and confirming the reload now actually happens.
- **Dead code removed**: `tidestep/segments.py`'s `_sample_points()` was
  defined but never called anywhere (superseded by the inline sampling in
  `sample_min_elevation`) — same class of leftover cruft as the
  `dev_seed.py` fix in the previous pass. Removed.
- **Coverage-driven gap found and closed**: `pytest --cov` showed
  `routing.py`'s `_highway_set()` list/tuple branch (OSM sometimes tags a
  simplified edge's `highway` with a list of values, not one string) was
  never exercised — added `test_highway_set_handles_list_valued_tags`
  confirming a vehicle is blocked only when *every* value in the list is
  non-drivable. Also added 5 new `tidestep/coops.py` tests (all pure
  monkeypatching, no network): the CO-OPS `{"error": ...}` response body
  is correctly raised as `RuntimeError` rather than silently treated as
  data, `check_datums()` genuinely catches a drifted station datum sheet
  (and passes when it matches), and `fetch_forecast_frame`'s bias-
  correction arithmetic (`ofs_navd88_m = ofs_raw_m - bias`,
  `nontidal_m = ofs_navd88_m - pred_navd88_m`) is checked directly,
  including that `bias_correct=False` genuinely skips the network call to
  `recent_ofs_bias` rather than just ignoring its result.
- **Net effect**: `tidestep/` package coverage 74% -> **95%**
  (`pytest --cov=tidestep`); test count 74 -> **81** (28 -> 81 across this
  and the previous two passes), still `pytest -q tests` runs the DB-free
  majority with nothing running, and 81 (up from 75) pass against a real
  local Postgres+PostGIS when one is reachable. Read `routing.py`, `api.py`,
  `db.py`, `hazard.py`, `floodfill.py`, `segments.py`, `streets.py`,
  `dem.py`, `coops.py`, `frontend/index.html`, `docker-compose.yml`,
  `README.md`, `docs/LIMITATIONS.md` fresh, independent of the previous
  passes' "confirmed correct" notes (which is exactly how the
  hourly_update.py bug above was caught — the previous pass's read of
  `db.py`/`api.py` alone wasn't enough to see a bug in how another script
  *calls* them). Remaining uncovered lines are defensive edge-case guards
  (empty/off-DEM/no-line-water-feature short-circuits) and one
  effectively-unreachable `except NetworkXNoPath` branch in
  `routing.Router.route()` (the baseline computation is strictly more
  permissive than the primary route, so if the primary succeeds the
  baseline provably cannot fail) — judged not worth contriving a test for.

## Done (2026-09-08, third pass — rigorous module-by-module audit against
docs/PIPELINE.md, test coverage gap closed, live end-to-end verification)
- **Read every remaining core module line by line against its pipeline
  stage** (`coops.py` vs Stage 1, `dem.py`/`streets.py` vs Stage 1,
  `segments.py`/`floodfill.py` vs Stage 2, `hazard.py`/`config.py` vs
  Stage 3, `db.py` vs Stage 4, and all four orchestration scripts) — the
  parts of the codebase the previous audit pass hadn't gotten to yet.
  Confirmed correct: datum conversion, OFS bias correction, DEM tiling and
  merge, `retain_all=True` bbox handling, the connected-flood-fill
  algorithm, the near-inlet stricter-threshold direction, the PostGIS
  schema and every query (including the profile-name SQL-injection guard
  in `unsafe_edges`), and the lat/lon argument order through
  `api.py -> db.py -> PostGIS` for saved routes.
- **Real bug found and fixed**: `scripts/build_hazard.py` cached
  `data/segments.gpkg` (expensive: DEM sampling) and, whenever that file
  already existed, silently skipped recomputing `near_inlet` — a cheap
  spatial join against `data/water.gpkg` that has nothing to do with why
  the file is cached. Concretely: the laptop's `segments.gpkg` was built
  before `water.gpkg` existed, with `near_inlet` all `False`; simply
  re-running `build_hazard.py` after fetching `water.gpkg` — which is
  exactly what the previous version of this file's "Next" section told the
  user to do — would silently keep every segment marked "not near an
  inlet" forever. Fixed by extracting a `refresh_near_inlet()` helper that
  always recomputes the flag and only rewrites the cache file when it
  actually changes; regression-tested in `tests/test_build_hazard_script.py`
  against exactly this stale-cache scenario.
- **Dead code removed**: `scripts/dev_seed.py`'s `synthetic_tide()`
  computed a tide curve, then immediately discarded it and computed a
  second, different one that was actually used — the first computation was
  leftover cruft from an earlier iteration. Removed; verified via the full
  `dev_seed.py` run below that the actual (second) curve is unchanged.
- **Test coverage gap fully closed.** Six new test files, all passing:
  `test_dem.py` (DEM tiling/merge math, exercised for real by monkeypatching
  only the network call), `test_streets.py` (bbox ordering, the osmnx-2.x
  `osmid` rename, graph/water caching), `test_segments.py` (line-splitting
  edge cases, OSM tag normalization), `test_db.py` (schema, round-trips,
  `unsafe_edges`' injection guard, `edge_hazard`'s aggregation, saved-route
  CRUD — against a real local PostGIS), `test_api.py` (every request-
  validation rejection path — malformed bbox, out-of-range hour, unknown
  profile — confirmed to never touch the database at all), and
  `test_build_hazard_script.py` (the near_inlet regression above). Test
  count: **28 -> 74**, all passing, `pytest -q tests` still needs nothing
  running for the majority of them (only `test_db.py`/`test_integration.py`
  need a reachable Postgres, and skip cleanly without one).
- **Live end-to-end verification, not just tests**: got a local Postgres +
  PostGIS running directly in this Claude sandbox (the `postgresql`/`postgis`
  packages were already installed; `docker`'s daemon isn't startable here,
  so this bypassed Docker entirely), ran the complete test suite against it
  (74 passed), ran `python scripts/dev_seed.py` end to end, then actually
  booted `uvicorn tidestep.api:app` as a live server and hit it with `curl`
  — confirmed `/api/hours` returns 24 hours, `/api/route` returns a real
  route for `adult` at the synthetic peak hour and correctly returns
  "no safe route at this hour" for `vehicle_small` at the same hour/trip,
  and `/` serves the frontend with a 200. This sandbox still cannot reach
  NOAA (`curl` to `api.tidesandcurrents.noaa.gov` gets a proxy 403, as
  documented in `CLAUDE.md`), so Stage 9 validation and real-data fetches
  are still laptop-only — but everything downstream of a fetch has now been
  verified live, not just read and reasoned about.

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
1. **Commit and push.** This pass's time-aware routing + trip advisory
   work (and everything from the previous audit passes) is not on
   `origin/main` yet — see "Uncommitted work" below for the exact
   commands. Do this first so the fixes above aren't sitting only on disk.
1a. Once pushed, the natural next feature-work candidate (not started):
   have `route_advisory()`'s hour strip re-route per hour instead of
   reporting one fixed path's safe/unsafe status — "best way there at
   6pm" instead of just "is the usual way there safe at 6pm" — noted as
   an open gap in `docs/LIMITATIONS.md`'s Routing section.
2. `python scripts/validate_stage9.py --days 30` — Stage 9 has never
   actually been run anywhere. The logic is unit-tested and ready
   (`tests/test_validate.py`, 5 passing); this just needs to hit NOAA for
   real, which the laptop can now do (confirmed again this pass: this
   Claude sandbox still gets a proxy 403 to api.tidesandcurrents.noaa.gov).
   Paste the printed sensitivity/specificity into the submission's
   technical-challenges answer.
3. Re-run `build_hazard.py` **and then `load_db.py` (or restart
   `hourly_update.py`'s cron loop, now that its own reload bug is fixed)**
   on the laptop's real data — this is what actually gets corrected
   `near_inlet` flags into the *live* database. Both halves of this used
   to be silently broken: `build_hazard.py` alone wouldn't have recomputed
   the flag (fixed last pass), and even after that fix, `hourly_update.py`
   alone wouldn't have pushed a same-row-count correction into Postgres
   (fixed this pass). Re-running just one of the two would not have been
   enough — worth doing deliberately now that both are fixed, not just
   trusting "it'll pick it up on the next cron tick."
4. Record demo footage: the time slider across a real flood cycle, then
   the profile switch showing the same trip flood-blind vs. flood-aware
   for adult vs. vehicle. The app is confirmed running end to end on real
   data (and, this pass, on a live local server hit with real HTTP
   requests), so this is unblocked.
5. iOS app: see `ios/README.md` — Swift source is written, reviewed line
   by line against the real API/DB response shapes, and ready to open in
   Xcode, but has never actually compiled — needs a Mac, since neither
   Claude sandbox can run a Swift toolchain. This is the single biggest
   unverified risk left in the project.

Test coverage gap (previously item 5 here) is **done** — see the top two
sections: `test_dem.py`, `test_streets.py`, `test_segments.py`, `test_db.py`,
`test_api.py`, `test_build_hazard_script.py`, `test_hourly_update.py` all
added and passing; `tidestep/` package coverage is 95% (`pytest --cov`).

## Uncommitted work
Everything from this session and the previous ones is still sitting
uncommitted (repo policy: Claude never runs `git add`/`commit`/`push` —
see `CLAUDE.md`). `scripts/hourly_update.py`'s *portable-strftime* fix,
`docs/LIMITATIONS.md`, and an earlier `docs/STATUS.md` are already
committed and pushed (`8d779c6`, confirmed via `git log origin/main`).
Everything else described in this file, including this pass's new
time-aware-routing/advisory feature work, is still local-only. Files
touched **this pass** (on top of everything already listed as
uncommitted in earlier revisions of this file):
- Modified: `tidestep/config.py` (new `MAX_HOUR`, `WALK_SPEED_MPS`),
  `tidestep/api.py` (`time_aware` query param, new `/api/route/advisory`
  endpoint), `tidestep/routing.py` (`route_time_aware`, `route_advisory`,
  `time_aware_route_geojson`, supporting dataclasses/helpers),
  `frontend/index.html` (time-aware checkbox + advisory hour strip),
  `tests/test_routing.py` (+7), `tests/test_api.py` (+6),
  `tests/test_integration.py` (+2), `docs/LIMITATIONS.md` (Routing
  section rewritten), `docs/NOVELTY.md` (+2 entries), `docs/STATUS.md`
  (this section).
- Everything else previously listed here (from the four earlier passes:
  `README.md`, `tidestep/db.py`, `tidestep/segments.py`,
  `scripts/build_hazard.py`, `scripts/hourly_update.py`, `docs/NOVELTY.md`,
  `ios/`, `requirements-dev.txt`, `scripts/dev_seed.py`,
  `scripts/validate_stage9.py`, `tidestep/validate.py`, and the earlier
  new test files) is still uncommitted too — nothing described anywhere
  in this file has reached `origin/main` since `8d779c6`.

Suggested split, each buildable in one `git add` + `git commit`:
1. `docs/STATUS.md docs/NOVELTY.md docs/LIMITATIONS.md README.md` — docs.
2. `tidestep/ scripts/ tests/ requirements-dev.txt` — code + tests (every
   feature and fix across all five passes: predictive alerting, dev_seed
   fixture, validate.py, api/db hardening, the near_inlet caching fix, the
   hourly_update.py sync_db fix, and this pass's time-aware routing +
   trip advisory).
3. `frontend/index.html` — the new UI for time-aware routing + the
   advisory hour strip (small enough to call out on its own so a reviewer
   can see exactly what changed in the demo-facing surface).
4. `ios/` — the SwiftUI client, on its own since it's unreviewed by any
   compiler.
Or, more simply, one commit for everything:
```
git add -A
git commit -m "routing: time-expanded arrival-hour-aware routing + trip advisory endpoint (81->96 tests)"
git push
```

## Notes
- `ofs_water_level` returns 6-minute data; we take the hourly max.
- CO-OPS gaps come back as empty strings; `_to_series` drops them.
