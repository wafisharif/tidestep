# Stage 9b — street-level ground truth

`ground_truth.csv` lists streets in the study area that official or press
sources say flooded on specific dates, with the peak water level the Kings
Point gauge recorded that day. `scripts/validate_streets.py` runs the
connected flood-fill at each documented peak and checks, per street, whether
the model floods it. Results go to `docs/VALIDATION.md`.

Why this exists: `scripts/validate_stage9.py` compares the model to the gauge
water level, which is the model's own input, so a high correlation there is
expected by construction. This file is the independent check the pipeline
called for.

## Sources

* NWS New York (OKX) coastal flood impact catalog for Kings Point 8516945,
  https://www.weather.gov/media/okx/coastalflood/Kings%20Point%20impacts.pdf
  (Port Washington entries at 10.2 and 10.8 ft MLLW).
* NWS OKX top-20 water levels for Kings Point,
  https://www.weather.gov/media/okx/coastalflood/top20/Kings%20Point.pdf
* Patch, "Shore Road In Port Washington Reopens After 'Severe Flooding'",
  2022-12-23,
  https://patch.com/new-york/portwashington/shore-road-port-washington-closed-due-severe-flooding-police
* Village of Baxter Estates / Wikipedia, Shore Road (Port Washington):
  seawall defects and post-storm flooding on the Baxter Estates stretch.

## Caveats

* Reports are not exhaustive: a street the model floods that no source
  mentions is not a false positive, it is unverified. Only the listed
  streets are scored.
* "Port Washington Estates" (2017/2019 entry) is a neighborhood, not a
  street, and is not scored.
* Peaks are the NWS-catalogued values in ft MLLW, converted with the
  station's own datum sheet (NAVD88 = MLLW − 4.21 ft). Wind setup and
  waves on the day are not modeled; the catalog notes wave overtopping on
  some Sound-facing sites, which is why only bay-side Port Washington
  streets are used.
