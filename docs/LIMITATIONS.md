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

- **Hazard is evaluated at departure hour for the whole trip.** A route
  that takes long enough to cross an hour boundary is not re-checked
  against the hazard state for the hour a traveler would actually be on
  each later segment. For a bbox this size (walk/drive times of a few
  minutes to ~20 minutes) this rarely matters, but it is a known
  simplification, not an oversight.
- **MVP router is a full graph recompute per query** (networkx Dijkstra),
  which is fine at this bbox's scale but would not scale city-wide without
  moving to something like OSRM with hourly traffic-speed-file swaps.

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
