"""Stage 9b: check the model against streets that are DOCUMENTED to have
flooded (NWS impact catalog, press reports) — see docs/validation/README.md.

    python scripts/validate_streets.py            # writes docs/VALIDATION.md
    python scripts/validate_streets.py --json     # machine-readable to stdout

No network needed: peaks come from docs/validation/ground_truth.csv. Needs
data/dem_1m.tif, data/segments.gpkg (and water.gpkg) from fetch_all +
build_hazard. Runs one flood-fill per distinct peak (about 5-10 s each).

Scoring, per (event, street):
  hit        expect_flooded and >=1 matching segment is flooded at the peak
  miss       expect_flooded and none flooded
  correct    not expect_flooded and none flooded (negative control)
  false pos  not expect_flooded and >=1 flooded
Also reported: the fraction of the street's segments flooded and the
deepest predicted water, so a "hit" on one 15 m dip is distinguishable from
a street that is under water end to end.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tidestep import config, floodfill, hazard, replay  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GT = ROOT / "docs" / "validation" / "ground_truth.csv"
OUT = ROOT / "docs" / "VALIDATION.md"


def ft_mllw_to_m_navd88(ft: float) -> float:
    return (ft - config.MLLW_TO_NAVD88_FT) * config.FT_TO_M


# Longitude bounds per town so "Shore Road" in Port Washington is not confused
# with West/East Shore Road on the Great Neck side of the bay.
TOWN_LON = {"Port Washington": (-73.715, -73.69), "Great Neck": (-73.78, -73.715),
            "Kings Point": (-73.78, -73.73)}


def street_mask(segments: pd.DataFrame, street: str, town: str | None = None) -> np.ndarray:
    """Exact (case-insensitive) name match; OSM multi-names like
    'Shore Drive;Hemlock Drive' count if any part matches."""
    target = street.strip().lower()
    names = segments["name"].fillna("").str.lower()
    m = names.apply(lambda n: target in [x.strip() for x in n.split(";")]).to_numpy().copy()
    if town in TOWN_LON:
        lo, hi = TOWN_LON[town]
        lon = segments.geometry.representative_point().x.to_numpy()
        m &= (lon >= lo) & (lon <= hi)
    return m


def run(gt: pd.DataFrame) -> list[dict]:
    inp = replay.load_inputs()
    segs, dem, seeds = inp["segments"], inp["dem"], inp["seeds"]
    ground = segs["ground_m"].to_numpy(dtype="float64")
    results = []
    for peak_ft, group in gt.groupby("peak_ft_mllw", sort=True):
        wl = ft_mllw_to_m_navd88(float(peak_ft))
        mask = floodfill.connected_flood_mask(dem, seeds, wl)
        flooded = floodfill.segment_flooded(segs["min_row"], segs["min_col"], mask)
        depth_cm = np.where(flooded, np.maximum(wl - ground, 0.0), 0.0) * 100
        for _, row in group.iterrows():
            m = street_mask(segs, row.street, row.town)
            n = int(m.sum())
            n_fl = int((flooded & m).sum())
            expect = str(row.expect_flooded).lower() == "true"
            if n == 0:
                verdict = "street not in segments"
            elif expect:
                verdict = "hit" if n_fl else "miss"
            else:
                verdict = "correct (dry)" if n_fl == 0 else "false positive"
            results.append({
                "event_date": row.event_date, "peak_ft_mllw": float(peak_ft),
                "peak_m_navd88": round(wl, 3), "street": row.street, "town": row.town,
                "expect_flooded": expect, "segments": n, "segments_flooded": n_fl,
                "fraction_flooded": round(n_fl / n, 2) if n else None,
                "max_depth_cm": int(np.nanmax(depth_cm[m])) if n else None,
                "verdict": verdict, "source": row.source,
            })
    return results


def summarize(results: list[dict]) -> dict:
    scored = [r for r in results if r["verdict"] != "street not in segments"]
    pos = [r for r in scored if r["expect_flooded"]]
    neg = [r for r in scored if not r["expect_flooded"]]
    return {
        "reported_streets": len(pos),
        "hits": sum(r["verdict"] == "hit" for r in pos),
        "negative_controls": len(neg),
        "negatives_correct": sum(r["verdict"] == "correct (dry)" for r in neg),
        "unmatched_street_names": [r["street"] for r in results
                                   if r["verdict"] == "street not in segments"],
    }


def write_markdown(results: list[dict], summary: dict) -> str:
    lines = ["# Stage 9b — validation against documented street flooding", "",
             f"Generated {datetime.now():%Y-%m-%d %H:%M} by `scripts/validate_streets.py` "
             f"from `docs/validation/ground_truth.csv`.", "",
             f"**Reported flooded streets caught: {summary['hits']} / "
             f"{summary['reported_streets']}.** "
             f"Negative controls (normal high tide, MHHW) kept dry: "
             f"{summary['negatives_correct']} / {summary['negative_controls']}.", ""]
    if summary["unmatched_street_names"]:
        lines += ["Street names not found in the segment table (check OSM naming): "
                  + ", ".join(sorted(set(summary["unmatched_street_names"]))), ""]
    lines += ["| Event | Peak (ft MLLW / m NAVD88) | Street | Expected | Segments flooded | Max depth | Verdict |",
              "|---|---|---|---|---|---|---|"]
    for r in results:
        frac = "" if r["fraction_flooded"] is None else f"{r['segments_flooded']}/{r['segments']} ({r['fraction_flooded']:.0%})"
        depth = "" if r["max_depth_cm"] is None else f"{r['max_depth_cm']} cm"
        lines.append(f"| {r['event_date']} | {r['peak_ft_mllw']} / {r['peak_m_navd88']:.2f} | "
                     f"{r['street']} | {'flooded' if r['expect_flooded'] else 'dry'} | {frac} | {depth} | "
                     f"**{r['verdict']}** |")
    lines += ["", "## How to read this", "",
              "A *hit* means at least one 15 m segment of the named street is inside the "
              "connected flood region at the documented peak. The fraction and depth "
              "columns show how much of the street the model floods, so a hit on a single "
              "dip is visible as such. Reports are not exhaustive, so streets the model "
              "floods that no source mentions are unverified, not wrong; only the listed "
              "streets are scored. Sources and caveats: `docs/validation/README.md`."]
    notes = ROOT / "docs" / "validation" / "notes.md"
    if notes.exists():
        lines += ["", notes.read_text().rstrip()]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    gt = pd.read_csv(GT)
    results = run(gt)
    summary = summarize(results)
    if args.json:
        print(json.dumps({"summary": summary, "results": results}, indent=2, default=str))
        return
    md = write_markdown(results, summary)
    Path(args.out).write_text(md)
    print(md)


if __name__ == "__main__":
    main()
