"""TideStep constants. Stage 0 scope decisions and fixed reference values.

Everything here is a fixed reference for the chosen station and study area.
Nothing here needs a live API call. See docs/SCOPE.md for sources.
"""

# --- Reference station -----------------------------------------------------
STATION_ID = "8516945"
STATION_NAME = "Kings Point, NY"
STATION_LAT = 40.8103
STATION_LON = -73.7649

# --- Study area (Manhasset Bay shoreline) ----------------------------------
# (south, west, north, east) in WGS84 degrees
BBOX = (40.800, -73.750, 40.840, -73.700)

# --- Forecast window -------------------------------------------------------
FORECAST_HOURS = 24
REFRESH_MINUTES = 60

# --- Datums for 8516945, feet above station datum (STND) --------------------
# Epoch 1983-2001, accepted 2020-11-12. Source: CO-OPS metadata API.
DATUMS_FT_STND = {
    "MLLW": 12.88,
    "MLW": 13.16,
    "MSL": 16.77,
    "MTL": 16.74,
    "NAVD88": 17.09,
    "MHW": 20.33,
    "MHHW": 20.68,
}
FT_TO_M = 0.3048

# Offset to convert a CO-OPS water level reported relative to MLLW into
# NAVD88 (the vertical datum of the 3DEP DEM):  navd88 = mllw - MLLW_TO_NAVD88
MLLW_TO_NAVD88_FT = DATUMS_FT_STND["NAVD88"] - DATUMS_FT_STND["MLLW"]  # 4.21 ft
MLLW_TO_NAVD88_M = MLLW_TO_NAVD88_FT * FT_TO_M                          # 1.283 m


def mllw_to_navd88_m(level_m_mllw: float) -> float:
    """Convert a water level in metres above MLLW to metres above NAVD88."""
    return level_m_mllw - MLLW_TO_NAVD88_M


# --- NWS flood thresholds at Kings Point ------------------------------------
# Feet above STND from the CO-OPS floodlevels product. Converted to NAVD88 m.
FLOOD_THRESHOLDS_FT_STND = {
    "nws_minor": 22.89,
    "nws_moderate": 23.39,
    "nws_major": 25.89,
    # NOS values derived with the NOS CO-OPS 086 methodology, for comparison
    "nos_minor": 22.64,
    "nos_moderate": 23.55,
    "nos_major": 24.84,
}
FLOOD_THRESHOLDS_M_NAVD88 = {
    k: (v - DATUMS_FT_STND["NAVD88"]) * FT_TO_M
    for k, v in FLOOD_THRESHOLDS_FT_STND.items()
}

# --- Still-water depth safety thresholds (metres) ---------------------------
# Sources: UK Environment Agency FD2321 "Flood Risks to People" and
# UNSW Water Research Laboratory / Australian Rainfall & Runoff Book 6
# (Smith, Davey & Cox 2014). Depth*velocity limits are kept for reference
# even though velocity is assumed ~0 in the ponding model.
DEPTH_LIMIT_M = {
    "child": 0.5,
    "adult": 1.2,
    "vehicle_small": 0.3,
    "vehicle_large": 0.4,
    "vehicle_4wd": 0.5,
}
DEPTH_VELOCITY_LIMIT_M2S = {
    "child": 0.4,
    "adult": 0.6,   # conservative end of the 0.6-0.8 band
}

# Segments flagged as near an inlet / culvert / narrow channel crossing get a
# stricter band: their effective depth limit is multiplied by this factor.
INLET_SAFETY_FACTOR = 0.5

# --- Flood-fill seeds ---------------------------------------------------------
# 3DEP hydro-flattens open water. In the Manhasset Bay tile the bay surface
# sits at about -1.1 m NAVD88 (35% of pixels are <= -1.0 m and form one
# connected component touching the raster edge). Any pixel at or below this
# value is treated as open water and seeds the connected flood-fill.
SEED_ELEVATION_M = -1.0

# --- Road segmentation ------------------------------------------------------
SEGMENT_LENGTH_M = 15
