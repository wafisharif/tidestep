# Status

Updated: 2026-09-06

## Done
- Stage 0: scope locked (docs/SCOPE.md, tidestep/config.py). Station =
  Kings Point 8516945, bbox = Manhasset Bay, 24h hourly, still-water ponding.
  Datums and NWS flood thresholds pulled from the CO-OPS metadata API and
  hardcoded.

## Next
- Stage 1: CO-OPS client (ofs_water_level, predictions, datums), 3DEP DEM
  bbox fetch, osmnx street graph.

## Blocked / notes
- Pushing needs GitHub credentials on Allison's Mac.
- `ofs_water_level` returns 6-minute data regardless of `interval=h`;
  resample to hourly in code.
