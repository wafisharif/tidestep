# Status

Updated: 2026-09-06

## Done
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

## Next
- `data/water.gpkg` (OSM water features) had not downloaded yet when
  build_hazard ran, so near_inlet is all False. Re-run once it exists.
- Run `python scripts/check_ofs_bias.py`: the OFS-minus-predictions
  column was 0.4-1.1 m all day, which is either real weather or a datum
  mismatch in the OFS product. Must be settled before Stage 9.
- Stage 4-5: PostGIS schema + FastAPI endpoints.

## Notes
- `ofs_water_level` returns 6-minute data; we take the hourly max.
- CO-OPS gaps come back as empty strings; `_to_series` drops them.
