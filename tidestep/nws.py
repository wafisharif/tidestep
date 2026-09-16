"""Live NWS alerts for the study area (Coastal Flood Advisory / Warning ...).

Pulls https://api.weather.gov/alerts/active?point=lat,lon — the official
NWS alert feed for the point where the Kings Point gauge sits — and keeps
the result in a small in-process cache so the map can poll it freely.
The NWS API needs a descriptive User-Agent and nothing else.

The point of showing this next to the model is honesty: the official
warning and TideStep's street-level forecast come from the same water, so
a user (or judge) can see whether they agree.
"""
from __future__ import annotations

import time

import requests

from . import config

TIMEOUT = 10

# alert "event" names we consider relevant to coastal flooding, in the order
# we want them to sort (most serious first)
COASTAL_EVENTS = [
    "Coastal Flood Warning",
    "Storm Surge Warning",
    "Coastal Flood Advisory",
    "Coastal Flood Watch",
    "Storm Surge Watch",
    "Coastal Flood Statement",
    "High Surf Advisory",
    "Flood Warning",
    "Flood Advisory",
    "Flood Watch",
]

_cache: dict = {"at": 0.0, "data": None}


def _fetch(lat: float, lon: float) -> list[dict]:
    r = requests.get(config.NWS_ALERTS_URL, params={"point": f"{lat:.4f},{lon:.4f}"},
                     headers={"User-Agent": config.NWS_USER_AGENT,
                              "Accept": "application/geo+json"},
                     timeout=TIMEOUT)
    r.raise_for_status()
    feats = r.json().get("features", [])
    out = []
    for f in feats:
        p = f.get("properties", {})
        out.append({
            "id": p.get("id"),
            "event": p.get("event"),
            "severity": p.get("severity"),
            "headline": p.get("headline"),
            "onset": p.get("onset"),
            "ends": p.get("ends") or p.get("expires"),
            "sender": p.get("senderName"),
            "description": (p.get("description") or "")[:1200],
            "coastal": p.get("event") in COASTAL_EVENTS,
        })
    rank = {e: i for i, e in enumerate(COASTAL_EVENTS)}
    out.sort(key=lambda a: rank.get(a["event"], len(rank)))
    return out


def active_alerts(lat: float = config.STATION_LAT, lon: float = config.STATION_LON,
                  max_age_s: int = config.NWS_CACHE_SECONDS) -> dict:
    """Cached list of active alerts. Never raises: on a network error the
    last good result is returned (or an empty list with ``error`` set)."""
    now = time.time()
    if _cache["data"] is not None and now - _cache["at"] < max_age_s:
        return _cache["data"]
    try:
        alerts = _fetch(lat, lon)
        data = {"fetched_at": now, "alerts": alerts,
                "coastal_alerts": [a for a in alerts if a["coastal"]], "error": None}
        _cache.update(at=now, data=data)
        return data
    except Exception as e:  # keep serving the previous result if there is one
        if _cache["data"] is not None:
            stale = dict(_cache["data"])
            stale["error"] = f"refresh failed: {e}"
            return stale
        return {"fetched_at": now, "alerts": [], "coastal_alerts": [], "error": str(e)}


def clear_cache() -> None:
    _cache.update(at=0.0, data=None)
