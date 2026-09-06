"""Stage 4: load data/segments.gpkg and data/hazard.csv into PostGIS.

Needs the database running (docker compose up -d) and DATABASE_URL set
if it is not the docker-compose default.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geopandas as gpd  # noqa: E402
import pandas as pd  # noqa: E402

from tidestep import db  # noqa: E402
from tidestep.dem import DATA_DIR  # noqa: E402


def main():
    engine = db.get_engine()
    db.init_schema(engine)
    segs = gpd.read_file(DATA_DIR / "segments.gpkg")
    print("segments:", db.load_segments(engine, segs))
    hz = pd.read_csv(DATA_DIR / "hazard.csv", parse_dates=["valid_time"])
    wl = pd.read_csv(DATA_DIR / "water_levels.csv", index_col=0)
    bias = float(wl["ofs_bias_m"].iloc[0]) if "ofs_bias_m" in wl else 0.0
    print("hazard rows:", db.load_hazard(engine, hz, bias))
    print("hours:", len(db.valid_times(engine)))


if __name__ == "__main__":
    main()
