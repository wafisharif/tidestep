# TideStep pipeline (as built)

This is a rewrite of the team's original plan, kept in place below in the
git history for anyone who wants to see it. It's rewritten because the
gap between plan and reality had grown large enough to mislead a reader
using this doc as a map of the codebase: the original document says
"three endpoints," the app has sixteen; it says a two-column hazard key,
the schema has a three-column one and a migration to get there; it
describes ten stages, the app has eleven, plus a validation sub-stage
that didn't exist when the plan was written. The core ideas in the
original plan — bathtub-plus-connectivity flood-fill, per-profile depth
thresholds, a flood-avoiding router, an hourly refresh loop, honest
up-front limitations — are all still exactly right, and still exactly
what's implemented. What follows is what's actually there, stage by
stage, each cross-referenced to the module or script that implements it.

## Stage 0 — Scope decisions

Reference station: **NOAA CO-OPS 8516945, Kings Point, NY**
(`config.STATION_ID`), on Manhasset Bay, western Long Island Sound.
Study area bounding box: `config.BBOX = (40.795, -73.775, 40.845,
-73.695)` — about 6.7 km × 5.5 km, covering the Kings Point gauge, the
Great Neck / Kings Point / Great Neck Estates west shore, Manhasset Bay
itself, and the Manorhaven / Port Washington Shore Road east shore (see
the comment in `config.py` for why the bbox has to span both shores: an
earlier, tighter box cut the gauge off and osmnx kept only one shore
since the two only connect by road south of the bay).

Forecast window: **36 hours**, refreshed hourly (`config.FORECAST_HOURS
= 36`, `REFRESH_MINUTES = 60`). The original plan said 24; NYOFS guidance
actually covers 48 h per cycle, and 36 keeps a safety margin without
reaching the edge of what the model's guidance is good for.

Scope is still, deliberately, **still-water tidal ponding**, not
fast-flowing storm surge or riverine flooding — velocity is assumed near
zero except for a stricter threshold band applied near known inlets
(Stage 3). This is unchanged from the original plan and is still the
single most important scope line in the project: it's what makes a
laptop-scale flood-fill model honest instead of a toy.

## Stage 1 — Data acquisition

**Water levels** (`tidestep/coops.py`): CO-OPS `ofs_water_level`
(NYOFS model guidance, folds in wind setup) for the forecast, plain
`predictions` as a clear-weather baseline, `datums` for the
MLLW↔NAVD88 conversion (`config.mllw_to_navd88_m`) — all as planned.
One addition the plan didn't anticipate: `coops.recent_ofs_bias()`
compares the last 48 h of NYOFS forecast against the gauge's own
observed water level and returns the mean error, which `scripts/
build_hazard.py` subtracts from the forecast before it ever reaches the
flood-fill. NYOFS forecasts turned out to run consistently biased high
at this gauge; removing that bias was necessary to get the street-level
validation results in Stage 9b to line up.

**Elevation** (`tidestep/dem.py`): USGS 3DEP 1 m LiDAR DEM, streamed
per-tile from the 3DEP AWS bucket and merged for the study bbox, as
planned.

**Street network** (`tidestep/streets.py`): OSM via osmnx for the study
bbox, as planned, plus a `fetch_water()` for the open-water polygons the
flood-fill's seed pixels are validated against.

**Shelter buildings** (`tidestep/shelters.py`, Stage 11, not in the
original plan): schools, hospitals, fire/police stations, and community
centers fetched from OSM (`amenity=school|hospital|...`) for
`route_to_safety`'s shelter-preference feature.

**Live NWS alerts** (`tidestep/nws.py`, not in the original plan): active
coastal-flood alerts for the gauge from `api.weather.gov`, fetched
on-demand and cached for 10 minutes; not part of the hourly hazard
recompute, a separate always-live signal surfaced in the frontend banner.

**Thresholds** (`tidestep/config.py`): NWS/NOS flood-impact thresholds
for 8516945, and the depth-based safety thresholds, hardcoded as planned
— now **six** profiles, not the original plan's implicit three: `child`
(0.5 m), `adult` (1.2 m), `wheelchair` (0.15 m — a caster-geometry
assumption, not literature-derived, called out in `LIMITATIONS.md`),
`vehicle_small` (0.3 m), `vehicle_large` (0.4 m), `vehicle_4wd` (0.5 m).
Depth×velocity limits (child 0.4 m²/s, adult 0.6 m²/s) are retained for
reference in `config.DEPTH_VELOCITY_LIMIT_M2S` even though velocity
itself still isn't modeled.

## Stage 2 — Flood-fill

Unchanged from plan: 15 m road segments (`config.SEGMENT_LENGTH_M`,
`tidestep/segments.py`), minimum DEM elevation per segment footprint, and
a connectivity-checked flood-fill (`tidestep/floodfill.py`) seeded from
pixels at or below `config.SEED_ELEVATION_M = -1.0` — the open-water
elevation for this DEM tile, chosen because 35% of the tile's pixels sit
at or below it and form one connected component touching the raster
edge. A segment is only flooded if it falls inside the connected
flood-fill region, exactly the NOAA Sea Level Rise Viewer-style
correction the plan called for — the naive "elevation < water level"
bathtub approach was never implemented.

## Stage 3 — Hazard classification

For each segment, for each forecast hour, for **each of four sea-level-
rise scenarios** (Stage 4b, not in the original plan): depth = scenario
water surface elevation minus segment ground elevation, inside the
connected flood region. `tidestep/hazard.py` classifies against the six
profile thresholds above; near-inlet segments (flagged in
`tidestep/segments.py`) get `config.INLET_SAFETY_FACTOR = 0.5` applied to
their effective depth limit, same stricter-band approach the plan
specified in place of real velocity. Each `(scenario_cm, segment_id,
forecast_hour)` row ends up with six boolean safety flags, raw depth in
cm, and — new — `grade_pct` (running grade from the DEM, for the
wheelchair profile's ADA slope check).

## Stage 4 — Storage

PostgreSQL + PostGIS, as planned, but the schema has grown one dimension
the plan didn't have: the hazard table's primary key is **`(scenario_cm,
segment_id, valid_time)`**, not the plan's `(segment_id, forecast_hour)`
— every row now belongs to one of four sea-level-rise scenarios
(`config.SLR_SCENARIOS_CM = [0, 30, 60, 100]`, NOAA 2022 Intermediate
projections for ~2050/2070/2100), computed and stored together on every
run. `tidestep/db.py`'s `init_schema()` carries an idempotent
`MIGRATIONS` block plus an explicit check
(`_HAZARD_PK_HAS_SCENARIO`) that rebuilds the primary key in place on a
database still on the original two-column schema, so this upgrade is
safe to run against an already-deployed instance without a manual
migration step. `shelters` (Stage 11) and `saved_routes` (Stage 8) are
the other two tables; `segments` and `forecast_runs` are as planned.

## Stage 5 — Backend API

FastAPI, as planned — but **sixteen endpoints**, not three:

| Endpoint | Purpose |
|---|---|
| `GET /api/config` | Static reference data (station, bbox, profiles, thresholds) |
| `GET /api/hours` | Forecast hours + flooded-segment counts, per SLR scenario |
| `GET /api/risk` | GeoJSON hazard state at one hour (bbox filter, flooded-only) |
| `GET /api/alerts` | Live NWS coastal-flood alerts |
| `GET /api/replay` | List of cached historical-replay days |
| `GET /api/replay/{date}/hours`, `/risk` | Replay a past day on observed water levels |
| `GET /api/route` | Flood-avoiding route (plain or time-aware) |
| `GET /api/route/advisory` | Hour-by-hour safe/unsafe forecast for one trip |
| `GET /api/route/best_departure` | Hour-by-hour *actual best* route, not just one path's status |
| `POST /api/route/multi_stop` | Ordered multi-stop route (optional stop-order optimization) |
| `GET /api/route/to_safety` | Evacuation routing to the nearest safe haven / shelter |
| `GET /api/network/chokepoints` | Network-wide structural single-points-of-failure |
| `GET /api/routes`, `POST /api/routes`, `DELETE /api/routes/{id}` | Saved routes for alerting |
| `GET /` | The Leaflet frontend |

The plan's third endpoint ("a saved-routes endpoint for the alerting
feature") is the only one of the original three whose scope didn't grow;
the risk-map and routing endpoints planned as one each are now families
of five and eight respectively, covering questions ("when is it safe to
leave," "where can I go that's safe," "which street is a structural
chokepoint") the original plan didn't pose at all.

## Stage 6 — Routing engine

Load the OSM graph with osmnx and run Dijkstra with networkx, exactly as
planned — but the plan's single "flood-avoiding shortest path" has grown
into a `Router` (`tidestep/routing.py`, ~460 lines) with **eight**
distinct capabilities: `route` (plain, plus `scenario_cm` for SLR),
`route_time_aware` (hazard checked at each segment's own arrival hour,
using per-profile walking speeds or OSM travel times — the plan's stated
MVP limitation, "hazard at departure time for the whole route," is
already fixed, not just planned around), `route_advisory` (hour-by-hour
safe/unsafe for one fixed trip), `route_best_departure` (hour-by-hour
*best available* route, which can find a safe detour at an hour
`route_advisory` would call unsafe because only the *usual* path
floods), `route_multi_stop` and `route_multi_stop_optimized` (each leg's
hazard check starts from the previous leg's actual arrival hour; the
optimized variant searches visiting order, bounded by
`MAX_OPTIMIZE_STOPS`), `route_to_safety` (no destination required —
nearest point that stays safe for the rest of the window, preferring a
real shelter building when `tidestep/shelters.py` data is loaded), and
the network-wide chokepoint analysis in `tidestep/resilience.py`
(bridge-edge detection on the profile-appropriate subgraph, ranked by
nodes isolated × hours unsafe — a genuinely different question from any
point-to-point route, added as Stage 11). The wheelchair profile also gets its own graph-edge filter
(`edge_allowed` excludes stairs and other non-wheelchair-accessible
highway types); its ADA grade limit is enforced upstream in Stage 3
(`hazard.py` marks a too-steep segment unsafe for `wheelchair` at every
hour from the DEM-derived `grade_pct`, so it's excluded the same way a
flooded segment is, not through a separate graph rule). The plan's "2.0" note about OSRM's hot-swappable edge weights for a
production version is still accurate and still not implemented — the
graph is still small enough to recompute per query.

## Stage 7 — Frontend

Leaflet, as planned, with the time slider (now with play/pause) built
early as the plan recommended. Beyond the plan's "form + profile
selector + route overlay": an SLR scenario selector, a wheelchair profile
option, a live NWS alert banner, a historical-replay date picker, an
"evacuate to safety" button, and a saved-routes panel (label, contact,
per-route flooded/clear status, delete) that finally gave the Stage 8
alerting feature a web UI — it existed on the backend and in the iOS app
for several passes before the web client could reach it at all. Responsive
phone layout, since a flood-safety tool is exactly the kind of thing
someone actually checks from a phone standing at a flooded corner.

## Stage 8 — Alerts and the operational loop

Unchanged in shape from the plan: `scripts/hourly_update.py` pulls fresh
CO-OPS data, recomputes the full segment/hour/scenario hazard grid,
writes it to PostGIS, checks every saved route against the new data, and
emails (SMTP, not the plan's Twilio/SendGrid SMS) the first hour a saved
route newly floods. Native push is still a stated 2.0 item, as planned.

## Stage 9 — Validation

**9a, gauge-level** (`scripts/validate_stage9.py`), as planned: retroactive
runs against real past high-tide-flooding days, checked against the NOAA
Annual High Tide Flooding Outlook and local reporting.

**9b, street-level** (`scripts/validate_streets.py`, `docs/validation/`,
not in the original plan — the plan's Stage 9 only checked "did the
connectivity logic look reasonable"): a ground-truth catalog
(`docs/validation/ground_truth.csv`) of specific streets documented as
flooded or dry on specific dates, built from the NWS impact catalog and
local press, scored hit/miss/correct/false-positive per street per event.
Current result: 5 of 9 documented street floodings reproduced, 4 of 4 dry
negative controls stayed dry, the 2022-12-23 Shore Road closure
reproduced on the exact documented Main St–Mill Pond Rd stretch, with
about a 0.2 m conservative bias on Shore Road specifically. Full
methodology and numbers in `docs/VALIDATION.md`.

## Stage 10 — Limitations

`docs/LIMITATIONS.md` — grown from the plan's three bullets (no
storm-drain modeling, no wave run-up, near-zero velocity assumption,
which are all still accurate and still stated) to a full document
covering every simplification introduced by stages 4b, 6, 9b, and 11
too: the wheelchair depth limit's caster-geometry basis rather than a
published threshold, the illustrative (not gridded) SLR offsets, the
walking-speed constants used for time-aware routing, and what street-
level validation does and doesn't prove.

## Stage 11 — Shelters and network resilience

Not in the original plan at all. Two additions grouped here because both
answer a "beyond one trip" question the original ten stages never posed:

- **Shelter-preferred evacuation** (`tidestep/shelters.py`,
  `Router.route_to_safety`'s `prefer_shelters` option): route to an actual
  shelter building when one is loaded and reachable, falling back to the
  original "any dry street" behavior otherwise — a strict upgrade, never
  a new failure mode.
- **Chokepoint / resilience analysis** (`tidestep/resilience.py`,
  `GET /api/network/chokepoints`): which street segments are structural
  single points of failure for the network as a whole, ranked by how many
  nodes they'd cut off and how many forecast hours they're actually
  unsafe — the "which specific road, if it floods, cuts part of the
  neighborhood off entirely" question a Congressional office evaluating
  infrastructure investment would actually ask, distinct from anything a
  point-to-point router answers.
