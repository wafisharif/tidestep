# Stage 9b — validation against documented street flooding

Generated 2026-09-15 12:00 by `scripts/validate_streets.py` from `docs/validation/ground_truth.csv`.

**Reported flooded streets caught: 5 / 9.** Negative controls (normal high tide, MHHW) kept dry: 4 / 4.

| Event | Peak (ft MLLW / m NAVD88) | Street | Expected | Segments flooded | Max depth | Verdict |
|---|---|---|---|---|---|---|
| calm (MHHW) | 7.8 / 1.09 | Shore Road | dry | 0/262 (0%) | 0 cm | **correct (dry)** |
| calm (MHHW) | 7.8 / 1.09 | Main Street | dry | 0/176 (0%) | 0 cm | **correct (dry)** |
| calm (MHHW) | 7.8 / 1.09 | Secor Drive | dry | 0/70 (0%) | 0 cm | **correct (dry)** |
| calm (MHHW) | 7.8 / 1.09 | Mill Pond Road | dry | 0/66 (0%) | 0 cm | **correct (dry)** |
| 2014-12-09 | 10.2 / 1.83 | Shore Road | flooded | 0/262 (0%) | 0 cm | **miss** |
| 2014-12-09 | 10.2 / 1.83 | Main Street | flooded | 0/176 (0%) | 0 cm | **miss** |
| 2014-12-09 | 10.2 / 1.83 | Secor Drive | flooded | 4/70 (6%) | 13 cm | **hit** |
| 2017-03-14 | 10.8 / 2.01 | Shore Road | flooded | 2/262 (1%) | 0 cm | **hit** |
| 2017-03-14 | 10.8 / 2.01 | Main Street | flooded | 0/176 (0%) | 0 cm | **miss** |
| 2019-12-30 | 10.8 / 2.01 | Shore Road | flooded | 2/262 (1%) | 0 cm | **hit** |
| 2019-12-30 | 10.8 / 2.01 | Main Street | flooded | 0/176 (0%) | 0 cm | **miss** |
| 2022-12-23 | 11.8 / 2.31 | Shore Road | flooded | 30/262 (11%) | 30 cm | **hit** |
| 2022-12-23 | 11.8 / 2.31 | Mill Pond Road | flooded | 18/66 (27%) | 16 cm | **hit** |

## How to read this

A *hit* means at least one 15 m segment of the named street is inside the connected flood region at the documented peak. The fraction and depth columns show how much of the street the model floods, so a hit on a single dip is visible as such. Reports are not exhaustive, so streets the model floods that no source mentions are unverified, not wrong; only the listed streets are scored. Sources and caveats: `docs/validation/README.md`.

## Interpretation (written 2026-09-15 after the first run)

* **The 2022-12-23 closure is reproduced spatially, not just as a yes/no.**
  Patch reported Shore Road closed between Main Street and Mill Pond Road.
  At the catalogued peak (11.8 ft MLLW = 2.31 m NAVD88) the model floods 30
  segments of Shore Road, all between latitude 40.8338 and 40.8356 — the
  stretch that starts just north of the Main Street junction (40.8326) and
  ends at Mill Pond Road (40.8357). Nothing else on the road floods. Mill
  Pond Road itself floods at its bay end (18 of 66 segments), matching the
  closure endpoint.
* **The model is about 0.2 m conservative on Shore Road.** Its lowest
  centerline sample is 2.01 m NAVD88 (≈10.8 ft MLLW), so the road first
  floods in the model at about 2.05 m, while NWS records minor flooding
  there from 10.2 ft (1.83 m). Two known reasons, both in
  `docs/LIMITATIONS.md`: (1) the peak is measured at the Kings Point gauge
  at the mouth of Manhasset Bay, and northeasterly wind setup inside the
  bay adds water at the Port Washington shore that the gauge does not see;
  (2) segment elevation is sampled along the OSM centerline, and the
  seawall-side edge of Shore Road is lower than its crown. A 0.2 m bias is
  the right number to quote to a judge; it is not hidden by the hit count.
* **"Sunset Park on Main Street" is the park, not the roadway.** Main
  Street's lowest segment is 2.80 m NAVD88; the waterfront park beside it
  is what the NWS statement describes flooding. The three Main Street rows
  are kept in the table as misses so the score is not flattered, but they
  should be read as a ground-truth wording issue.
* **Negative controls hold.** At a normal high tide (MHHW, 1.09 m) none of
  the four streets floods, so the model is not simply painting the
  waterfront wet at every tide.
* **Sample size is small.** Nine street-events from three sources is what
  exists publicly for this shoreline. Nassau County DPW road-closure logs
  or Port Washington police blotter archives would extend it; that request
  is noted in STATUS.md.
