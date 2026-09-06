"""Stage 1: pull every input the model needs into data/.

Run from the repo root (a normal terminal or a Jupyter cell with
``%run scripts/fetch_all.py``). Needs internet access to NOAA, USGS, and
Overpass.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tidestep import coops, dem, streets  # noqa: E402


def main():
    print("1/4 datums  ...", end=" ", flush=True)
    coops.check_datums()
    print("match config")

    print("2/4 water levels ...", end=" ", flush=True)
    wl = coops.fetch_forecast_frame()
    out = dem.DATA_DIR / "water_levels.csv"
    dem.DATA_DIR.mkdir(exist_ok=True)
    wl.to_csv(out)
    print(f"{len(wl)} hours -> {out}")
    print(wl.round(3).to_string())

    print("3/4 DEM ...", end=" ", flush=True)
    path = dem.fetch_dem()
    print(path)

    print("4/4 streets + water ...", end=" ", flush=True)
    G = streets.fetch_graph()
    water = streets.fetch_water()
    print(f"{G.number_of_nodes()} nodes, {G.number_of_edges()} edges, "
          f"{len(water)} water features")


if __name__ == "__main__":
    main()
