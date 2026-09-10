# Stage 10 — Known limitations

Stated up front, on purpose: these are deliberate scope decisions made to ship
a correct, explainable model in the time available, not gaps we didn't
notice. Each one is also called out in the module it applies to.

## Hydraulic model

- **Still-water ponding only.** The flood surface is modelled as a flat
  plane at the forecast water level, restricted to DEM cells hydrologically
  connected to the bay (`tidestep/floodfill.py`). We do not model fast-moving
  storm surge, wave run-up, or riverine/stormwater flooding. This is a
  correct scope choice for routine high-tide flooding — the everyday case —
  not for hurricane-driven surge, which would need a full hydrodynamic model
  (e.g. ADCIRC/SLOSH-class simulation), out of reach for this project's
  timeline.
- **Velocity is assumed ~0** except at segments flagged `near_inlet`
  (`tidestep/hazard.py`), which get a stricter depth limit
  (`INLET_SAFETY_FACTOR`) as a conservative stand-in for local flow
  acceleration near culverts/channels, instead of a computed velocity. This
  is reasonable for tidal backwater ponding away from constrictions; it is
  not a substitute for a true depth x velocity hazard rating in a
  fast-flowing scenario.
- **Storm drains are not modelled.** Real streets can flood less than
  predicted (drains carrying water away) or more (backflow through drains
  at high tide). The DEM-based model has no drainage-network layer.
- **Single reference station.** Water level is measured/forecast at one
  NOAA gauge (Kings Point, 8516945) and applied uniformly across the bbox.
  Real water surface slope across ~6 km of shoreline during wind events is
  not captured.
- **3DEP hydro-flattening.** The DEM represents open water as a flat
  surface at roughly -1.1 m NAVD88 rather than true bathymetry; the seed
  threshold (`SEED_ELEVATION_M = -1.0`) is tuned to this artifact, not to a
  physical water depth.

## Forecast

- **OFS bias correction is a constant offset.** `recent_ofs_bias()` removes
  the trailing 48-hour mean (NYOFS - observed) difference. This corrects a
  steady model bias but not timing errors (the model predicting the right
  peak height at the wrong hour), which would need a more sophisticated
  correction (e.g. dynamic time warping against the observed curve).
- **Threshold source mismatch is possible.** NWS flood thresholds
  (`FLOOD_THRESHOLDS_M_NAVD88`) are impact-based categories (property
  damage/life risk), not derived from the same depth-safety literature as
  the child/adult/vehicle thresholds. We use NWS levels only as a
  sanity-check reference, not as an input to the hazard classification.

## Routing

- **Hazard-at-departure-hour is now opt-in, not the only mode.**
  `Router.route_time_aware()` (`tidestep/routing.py`, exposed via
  `/api/route?time_aware=true`) is a genuine time-expanded shortest-path
  search: it converts each edge's length into actual time-on-segment
  (`config.WALK_SPEED_MPS` for pedestrians, osmnx's posted-speed-limit
  `travel_time` for vehicles), tracks cumulative elapsed time along the
  path, and checks hazard state at the forecast hour a traveler would
  *actually* be on each segment, not the hour they left. The plain
  `/api/route` (no `time_aware` flag, the default) keeps the original
  departure-hour-only behavior unchanged, byte-for-byte, for backward
  compatibility — this was verified directly (curl comparison of the two
  response shapes) rather than assumed. For this bbox's scale (walk/drive
  times of a few minutes to ~20 minutes) the two modes usually agree, but
  `tests/test_routing.py::test_route_time_aware_avoids_hazard_that_appears_after_departure`
  constructs a case where they provably don't, and only the time-aware
  mode gets it right. This is separate from `Router.route_window()` (used
  by `scripts/hourly_update.py`'s predictive-alert loop), which already
  scanned the full 24 h window for a *fixed* baseline path's first-unsafe
  hour — that mechanism is unchanged by this work.
- **Time-aware routing still assumes free-flow travel time.** It has no
  model of how flooding itself might slow a traveler down (wading through
  ankle-deep water is slower than the dry-pavement walking/driving speed
  used to compute elapsed time) — a second-order effect, not accounted
  for.
- **`route_advisory()`'s hour-by-hour forecast is against one fixed
  (flood-blind) path**, not a re-routed path per hour — it answers "is my
  usual way there safe at hour H," not "what's the best way there at hour
  H" for every hour. **Now addressed for the case where a real detour
  exists**: `Router.route_best_departure()` (`/api/route/best_departure`)
  recomputes the actual best route at every hour via `route_time_aware()`
  and reports the earliest hour a real route exists at all, not just
  whether the *usual* path is clear — proven with a synthetic case
  (`tests/test_routing.py::test_route_best_departure_finds_a_safe_detour_advisory_would_call_unsafe`)
  where `route_advisory()` reports every hour unsafe (the direct path
  never clears) while `route_best_departure()` correctly finds a longer
  but safe detour available immediately. This is more expensive (up to
  24 full shortest-path searches per call, one per candidate hour, vs.
  `route_advisory()`'s single search) — acceptable at this bbox's scale,
  the same tradeoff already accepted below for a full-graph-recompute
  router; not something a city-scale deployment could do unchanged. Note
  honestly: in the current synthetic dev_seed street topology, no real
  detour actually exists for the vehicle profiles once the direct route
  floods, so `route_best_departure()` and `route_advisory()` happen to
  agree there — the algorithm's extra value is proven by the controlled
  unit test, not (yet) visible in the synthetic demo data itself.
- **Multi-stop trips (`Router.route_multi_stop()`, `POST
  /api/route/multi_stop`) don't re-optimize stop order.** Waypoints are
  routed in the order given — there's no traveling-salesman-style
  reordering to find the shortest overall visiting order. For a small
  number of user-chosen stops (the common case: "school, then the
  grocery store, then home") this is the right behavior — a user has a
  reason for their order — but it means the endpoint won't suggest a
  smarter sequence on its own.
- **`route_to_safety()`'s "safe haven" is any point that stays flood-safe
  for the rest of the modeled window** (`db.always_safe_nodes()`) — it is
  not aware of which of those points are actually meaningful shelter
  (a school, a firehouse, high ground with parking) versus just a random
  dry street segment. Distinguishing real shelter locations from merely
  dry pavement would need a POI dataset this project doesn't currently
  load. It is also, like the rest of the router, still-water-ponding-only
  (see "Hydraulic model" above) — it has no concept of which direction a
  storm is moving or which shelter would still be reachable if conditions
  worsened beyond what NYOFS currently forecasts.
- **MVP router is a full graph recompute per query** (networkx Dijkstra
  for the plain router; a hand-rolled Dijkstra over `(elapsed_time, node)`
  state for the time-aware router, run up to once per forecast hour for
  `route_best_departure()`), which is fine at this bbox's scale but would
  not scale city-wide without moving to something like OSRM with hourly
  traffic-speed-file swaps.

## Alerts

- **Delivery is email only** (`scripts/hourly_update.py`); SMS/native push
  are 2.0 items, not implemented.
- **No authentication on `/api/routes`.** Saving, listing, and deleting a
  saved route requires no login — anyone with the API URL can see or
  delete anyone else's saved route. Acceptable for a single-user demo
  scope; a real multi-user deployment would need per-user accounts before
  this endpoint could be opened up publicly.

Superseded (kept here as a record, not a current gap): earlier builds
checked a saved route against forecast_hour=0 only, so an alert could only
fire once flooding had already started. `Router.route_window()` +
`scripts/hourly_update.py` now scan the whole 24 h forecast window per
saved route and report the first hour the usual path becomes unsafe, so an
alert can read "floods starting around 4:00 PM today" ahead of time.

## Validation

- **Stage 9 (retroactive validation against a real past high-tide-flooding
  day at Kings Point) has not been run yet.** This is the next priority
  before demo recording — see docs/STATUS.md.
