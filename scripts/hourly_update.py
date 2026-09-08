"""Stage 8: the hourly operational loop.

1. pull fresh NYOFS + predictions (bias-corrected) for the next 24 h
2. recompute the segment x hour hazard grid
3. write it to PostGIS
4. check every saved route against the new grid
5. notify a route's contact the first time it becomes blocked

Schedule with cron (every hour at :05):
    5 * * * * cd /path/to/tidestep && /path/to/python scripts/hourly_update.py >> data/hourly.log 2>&1

Notification channel: SMTP email if SMTP_HOST/SMTP_USER/SMTP_PASS/SMTP_FROM
are set in the environment; otherwise the alert is printed to the log.
Native push is a 2.0 item.
"""
import os
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geopandas as gpd  # noqa: E402
import osmnx as ox  # noqa: E402
import pandas as pd  # noqa: E402

from tidestep import coops, db, floodfill, hazard, routing, streets  # noqa: E402
from tidestep import dem as demmod  # noqa: E402

DATA = demmod.DATA_DIR


def notify(contact: str, subject: str, body: str) -> None:
    host = os.environ.get("SMTP_HOST")
    if not host or "@" not in contact:
        print(f"[alert] to={contact} :: {subject} :: {body}")
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, os.environ["SMTP_FROM"], contact
    msg.set_content(body)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", 587))) as s:
        s.starttls()
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)


def main():
    t0 = time.time()
    now = datetime.now(timezone.utc)
    print(f"--- hourly update {now.isoformat(timespec='minutes')}")

    # 1. water levels
    wl = coops.fetch_forecast_frame()
    wl.to_csv(DATA / "water_levels.csv")
    bias = float(wl["ofs_bias_m"].iloc[0])
    print(f"water levels: {len(wl)} h, peak {wl.ofs_navd88_m.max():.2f} m NAVD88, bias {bias:+.2f} m")

    # 2. hazard grid (segments + DEM are static; only the water level changes)
    dem, transform, meta = demmod.load_dem(DATA / "dem_1m.tif")
    segs = gpd.read_file(DATA / "segments.gpkg")
    water = gpd.read_file(streets.WATER_PATH) if streets.WATER_PATH.exists() else None
    seeds = floodfill.build_seed_mask(dem, transform, water, meta["crs"])
    table = hazard.hazard_table(segs, dem, seeds, wl["ofs_navd88_m"])
    table.to_csv(DATA / "hazard.csv", index=False)
    print(f"hazard: {table.flooded.sum()} flooded segment-hours")

    # 3. store
    engine = db.get_engine()
    db.init_schema(engine)
    with engine.connect() as conn:
        from sqlalchemy import text
        n_seg = conn.execute(text("SELECT count(*) FROM segments")).scalar_one()
    if n_seg != len(segs):
        db.load_segments(engine, segs)
    db.load_hazard(engine, table, bias)

    # 4-5. saved routes: check the WHOLE 24 h window, not just right now, so
    # the alert can say *when* flooding starts ("floods at 4pm today")
    # instead of only firing once it has already happened. This was the
    # stated intent in the comment above (previously the code only checked
    # forecast_hour=0) — see docs/STATUS.md.
    R = routing.Router(ox.load_graphml(streets.GRAPH_PATH), engine)
    hours = range(len(wl))
    valid_times = list(wl.index)
    for r in db.list_saved_routes(engine):
        win = R.route_window((r["olat"], r["olon"]), (r["dlat"], r["dlon"]),
                             r["profile"], hours)
        blocked = win.first_unsafe_hour is not None
        if blocked and not r["last_blocked"]:
            label = r["label"] or "your saved route"
            if win.baseline_length_m is None:
                body = (f"TideStep: {label} has no safe {r['profile']} route between "
                        f"these points at all, flooding aside — check the app for a "
                        f"different start/end point.")
            elif win.first_unsafe_hour == 0:
                body = (f"TideStep: the usual path for {label} is flooded right now "
                        f"(up to {win.max_depth_cm} cm, water level "
                        f"{wl.ofs_navd88_m.iloc[0]:.2f} m NAVD88 at Kings Point). "
                        f"Check the app for a safe detour.")
            else:
                when = valid_times[win.first_unsafe_hour].strftime("%-I:%M %p %Z")
                body = (f"TideStep: the usual path for {label} is on track to flood "
                        f"starting around {when} today (up to {win.max_depth_cm} cm). "
                        f"It is still clear right now — plan ahead or check the app "
                        f"for a detour before then.")
            notify(r["contact"], "TideStep flood alert", body)
        db.update_route_state(engine, r["route_id"], blocked)

    print(f"done in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
