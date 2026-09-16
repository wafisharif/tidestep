"""Precompute a historical replay so the web app serves it instantly.

    python scripts/replay_day.py 2022-12-23 [2019-12-30 ...]

Needs data/ from fetch_all.py + build_hazard.py and network access to NOAA
for the observed water levels. Output: data/replay/<date>.csv and a short
per-hour summary.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tidestep import replay  # noqa: E402


def main(dates):
    for d in dates:
        day = replay.parse_date(d)
        table = replay.replay_day(day, use_cache=False)
        replay.REPLAY_DIR.mkdir(parents=True, exist_ok=True)
        table.to_csv(replay.REPLAY_DIR / f"{d}.csv", index=False)
        peak = max(replay.hours_summary(table), key=lambda h: h["water_level_m"])
        print(f"{d}: peak {peak['water_level_m']:.2f} m NAVD88 at {peak['valid_time']}, "
              f"{peak['flooded_segments']} flooded segments")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
