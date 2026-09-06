# TideStep — Claude Cowork project notes

Shared repo for a two-person 2026 Congressional App Challenge (NY-03) team.
Both teammates run their own Claude session against this repo. Read this file
first in every session.

## Workflow rules
- Work directly in this cloned repo. Never work in a copy.
- `git pull --rebase` before starting anything.
- After every finished stage or meaningful sub-step: commit with a clear message
  and `git push` so the other teammate can see it.
- One stage per commit where possible; commit messages start with the stage
  number, e.g. `stage1: CO-OPS client + datum conversion`.
- Keep `docs/STATUS.md` current: what is done, what is next, what is blocked.
- Do not commit data downloads (DEM tiles, OSM caches). They go in `data/`
  which is git-ignored. Scripts must be able to regenerate them.

## What this project is
Hourly-refreshed 24h forecast of which street segments around a NOAA tide
station will pond at high tide, with child / adult / vehicle safety flags and
a flood-avoiding router. Full plan: `docs/PIPELINE.md`. Locked scope
decisions: `docs/SCOPE.md`. Constants and thresholds: `tidestep/config.py`.

## Stack
Python 3.11+, requests, rasterio, geopandas/shapely, osmnx, networkx,
FastAPI, PostGIS, Leaflet frontend. See `requirements.txt`.

## Environment note
Some Claude sandboxes cannot reach NOAA / USGS APIs directly. If a fetch
fails with a proxy 403, that is the sandbox, not the code. Run the fetch
scripts from a normal terminal or Jupyter on the laptop instead.
