"""Stage 9 CLI: run tidestep.validate against real historical data.

Needs data/segments.gpkg + data/dem_1m.tif from a prior fetch_all.py +
build_hazard.py run, and network access to NOAA (run from a normal
terminal, not a network-restricted sandbox). Writes data/validation.csv and
prints a summary suitable for pasting into the submission write-up.
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geopandas as gpd  # noqa: E402

from tidestep import dem as demmod, floodfill, streets, validate  # noqa: E402
from tidestep.validate import summarize  # noqa: E402

DATA = demmod.DATA_DIR


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30, help="number of random past days to check")
    ap.add_argument("--dates", type=str, default=None,
                    help="comma-separated YYYY-MM-DD list instead of a random sample")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    dem, transform, meta = demmod.load_dem(DATA / "dem_1m.tif")
    segs = gpd.read_file(DATA / "segments.gpkg")
    water = gpd.read_file(streets.WATER_PATH) if streets.WATER_PATH.exists() else None
    seeds = floodfill.build_seed_mask(dem, transform, water, meta["crs"])

    if args.dates:
        dates = [datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                for d in args.dates.split(",")]
    else:
        dates = validate.pick_sample_dates(args.days, seed=args.seed)

    results = []
    for d in dates:
        try:
            r = validate.validate_day(d, dem, seeds, segs)
            results.append(r)
            flag = "OK " if r.correct else "MISS"
            print(f"[{flag}] {r.date}  peak={r.peak_wl_m:5.2f} m  "
                 f"minor={'Y' if r.exceeds_minor else '.'} "
                 f"flooded_segs={r.flooded_segments_at_peak:3d}  "
                 f"depth={r.max_depth_cm_at_peak:3d} cm")
        except ValueError as e:
            print(f"[SKIP] {d.date()}: {e}")

    if not results:
        print("no days had usable data; nothing to summarize")
        return

    s = summarize(results)
    s["table"].to_csv(DATA / "validation.csv", index=False)
    print("\n--- summary (see data/validation.csv for the full table) ---")
    print(f"days checked:            {s['n_days']}")
    print(f"days >= NWS minor:       {s['n_exceeded_minor']}")
    print(f"days >= NWS moderate:    {s['n_exceeded_moderate']}")
    print(f"days >= NWS major:       {s['n_exceeded_major']}")
    sens = f"{s['sensitivity']:.0%}" if s["sensitivity"] is not None else "n/a (no minor-stage days sampled)"
    spec = f"{s['specificity']:.0%}" if s["specificity"] is not None else "n/a (no calm days sampled)"
    print(f"sensitivity (flooded when it should have): {sens}")
    print(f"specificity (dry when it should have been): {spec}")
    print(f"overall accuracy: {s['overall_accuracy']:.0%}")


if __name__ == "__main__":
    main()
