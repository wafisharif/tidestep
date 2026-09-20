# Status

Updated: 2026-09-20. Pass-by-pass history lives in `CHANGELOG.md`; this
page is only what is true now and what is next.

## What works

All ten pipeline stages plus the additions listed below run end to end on
real data for the Kings Point / Manhasset Bay study area (55,577 road
segments, 1 m DEM). 266 tests pass with `DATABASE_URL` set to a reachable
Postgres (0 skipped, 99% package coverage, `tidestep/api.py` at 100%) —
that includes the PostGIS integration/db suite, which now actually runs
in this sandbox (see `CHANGELOG.md`'s seventeenth pass); without a DB
reachable, 50 of those skip and 216 still pass. CI runs them on every
push (`.github/workflows/ci.yml`).

| Area | State |
|---|---|
| Data (Stage 1) | CO-OPS NYOFS + predictions + datums, USGS 3DEP DEM, OSM streets/water. NYOFS bias vs. gauge removed (trailing 48 h mean). |
| Flood model (2-3) | Connected flood-fill, 15 m segments with min elevation and running grade, per-profile safety flags. |
| Storage / API (4-5) | PostGIS, FastAPI. `/api/hours`, `/api/risk`, `/api/route*`, `/api/routes`, `/api/network/chokepoints`, `/api/alerts`, `/api/replay/...`. |
| Routing (6) | Dijkstra with unsafe edges removed; time-aware, best-departure, multi-stop, evacuate-to-shelter, chokepoints. |
| Web app (7) | Leaflet map, hour slider with play, click-to-route, alerts UI, phone layout. Loads only flooded segments for the whole area and full detail for the viewport. |
| Alerts (8) | Hourly job recomputes everything and emails (SMTP) the first hour a saved route floods. |
| Validation (9) | `validate_stage9.py` (gauge-level) and **`validate_streets.py` (street-level, against NWS impact catalog + press):** 5 of 9 documented street floodings reproduced, 4 of 4 dry controls dry, 2022-12-23 Shore Road closure reproduced on the exact Main St–Mill Pond Rd stretch, ~0.2 m conservative bias on Shore Road. See `VALIDATION.md`. |
| Limitations (10) | `LIMITATIONS.md`, updated for the items below. |

### Added 2026-09-15

- **Sea-level-rise scenarios**: every forecast is computed for +0/30/60/100
  cm (NOAA 2022 Intermediate, ~2050/2070/2100). Selector in the app;
  `slr_cm` on `/api/hours`, `/api/risk`, `/api/route`.
- **Wheelchair profile**: 0.15 m depth limit, no stairs, ADA 8.33 % grade
  limit from the DEM (`grade_pct` per segment).
- **Historical replay**: pick a past date, the model runs on the gauge's
  observed levels (`/api/replay/{date}/hours|risk`, `scripts/replay_day.py`).
- **Live NWS alert banner** from api.weather.gov (`/api/alerts`).
- **Street-level validation** (`docs/validation/`, `scripts/validate_streets.py`).
- **Packaging**: `Dockerfile`, API service in `docker-compose.yml`,
  `.env.example`, GitHub Actions CI.
- **Forecast window** 24 h → 36 h.
- Docs slimmed: this page; history moved to `CHANGELOG.md`.

### Added 2026-09-20

- **Full test suite validated against a real, running PostGIS instance**
  for the first time this project: schema creation, the idempotent
  migrations (including the legacy hazard-primary-key rebuild), and every
  raw SQL query function now have direct test coverage, not just the
  API-level mocked paths. 266 passed / 0 skipped / 99% coverage (see
  `CHANGELOG.md`'s seventeenth pass for the full breakdown).
- Fixed a real bug found in the process: `resilience.py`'s chokepoint
  priority score wasn't filtering to `scenario_cm = 0`, so it could mix
  hours-unsafe counts across sea-level-rise scenarios.

## To do

1. **Regenerate data on the laptop** after pulling: `python scripts/build_hazard.py`
   (rebuilds `segments.gpkg` once to add `grade_pct`, then all four scenarios,
   ~3 min) and `python scripts/load_db.py` (~2 min). The schema migration
   is automatic.
2. **Demo footage**: replay `2022-12-23` in the app, or wait for a forecast
   peak above NWS minor stage (1.77 m NAVD88).
3. **Extend ground truth**: ask Nassau County DPW / Port Washington PD for
   road-closure logs; add rows to `docs/validation/ground_truth.csv` and
   rerun.
4. **iOS**: the SwiftUI client has never been compiled. Build it in Xcode
   or leave it out of the submission. New JSON fields (`safe_wheelchair`,
   `grade_pct`, `scenario_cm`) are additive and will not break decoding.
5. **Hosting**: one `docker compose up` on any VPS now runs the whole app;
   a public URL still needs a host and the hourly cron set up there.
