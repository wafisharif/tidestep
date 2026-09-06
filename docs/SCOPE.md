# Stage 0 — Locked scope decisions

These were chosen so the rest of the pipeline has fixed inputs. Change them
here (and in `tidestep/config.py`) if the team decides otherwise.

## Reference station: Kings Point, NY — NOAA CO-OPS 8516945

- Location: 40.8103 N, 73.7649 W, on the Great Neck peninsula at the mouth
  of Manhasset Bay. Inside NY-03.
- It is a real-time NWLON station with NWS flood thresholds, NYOFS model
  guidance (`ofs_water_level` product confirmed returning data on
  2026-09-06), and an accepted datum sheet with a NAVD88 offset. That is
  every input the pipeline needs from one station.

## Datums (feet above station datum, epoch 1983-2001, accepted 2020-11-12)

| Datum  | ft STND |
|--------|---------|
| MLLW   | 12.88   |
| MSL    | 16.77   |
| NAVD88 | 17.09   |
| MHHW   | 20.68   |

Conversion used everywhere: `NAVD88 = MLLW - 4.21 ft` (= 1.283 m).
CO-OPS is queried with `datum=MLLW`; elevation data is NAVD88; every water
level is converted before comparison to ground.

## Flood thresholds at Kings Point (NWS)

| Level    | ft STND | ft MLLW | m NAVD88 |
|----------|---------|---------|----------|
| Minor    | 22.89   | 10.01   | 1.768    |
| Moderate | 23.39   | 10.51   | 1.920    |
| Major    | 25.89   | 13.01   | 2.682    |

Source: CO-OPS metadata API floodlevels for 8516945. NOS-derived values
(Technical Report NOS CO-OPS 086 method) are also stored in config for
comparison.

## Bounding box: Manhasset Bay and both shores

`(lat 40.795–40.845, lon -73.775 to -73.695)` — about 6.7 km × 5.5 km
(14 sq mi), covering the Kings Point gauge, Great Neck / Kings Point /
Great Neck Estates on the west shore, the bay, and Manhasset Isle /
Manorhaven / Port Washington's Shore Road on the east shore. The two shores
connect by road only south of the box, so the street graph is loaded with
`retain_all=True`. Still small enough to route on with plain networkx.

(Revised 2026-09-06: the first box, 40.80–40.84 / -73.75 to -73.70, left
the gauge outside and lost the whole west shore to osmnx's
largest-component filter.)

## Forecast window
24 hours ahead, hourly steps, refreshed hourly.

## What is modeled
Still-water tidal ponding: water surface = station forecast water level
(NYOFS, which already includes wind setup and short-term surge), applied as
a flat surface, restricted to DEM cells hydraulically connected to the bay.
Not modeled: wave run-up, fast-flowing surge, riverine flooding, storm
drains. See `docs/PIPELINE.md` Stage 10.
