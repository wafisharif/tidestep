# Status

Updated: 2026-09-06

## Done
- Stage 0: scope locked (docs/SCOPE.md, tidestep/config.py). Station =
  Kings Point 8516945, bbox = Manhasset Bay, 24h hourly, still-water ponding.
- Stage 1 (code): `tidestep/coops.py` (NYOFS + predictions + datums, hourly
  max, MLLW->NAVD88), `tidestep/dem.py` (3DEP ImageServer bbox export, tiled
  + merged), `tidestep/streets.py` (osmnx road graph + OSM water features).
  `scripts/fetch_all.py` runs all of it. Unit tests in `tests/` pass.

## Next
- Stage 1 (data): run `python scripts/fetch_all.py` from a laptop with
  internet (Claude sandboxes cannot reach NOAA/USGS). Check that
  `data/water_levels.csv` has 24 rows and `data/dem_1m.tif` opens.
- Stage 2: segment roads, sample min DEM elevation, connected flood-fill.

## Notes
- `ofs_water_level` returns 6-minute data; we take the hourly max.
- CO-OPS gaps come back as empty strings; `_to_series` drops them.
