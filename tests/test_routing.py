"""Router on a tiny hand-built graph; DB calls are stubbed."""
import networkx as nx
import pandas as pd

from tidestep import routing


def square_graph():
    # A(0,0) - B(1,0) direct edge (short, 100 m) plus a detour via C(0,1), D(1,1)
    G = nx.MultiDiGraph(crs="EPSG:4326")
    pts = {1: (0, 0), 2: (0.001, 0), 3: (0, 0.001), 4: (0.001, 0.001)}
    for n, (x, y) in pts.items():
        G.add_node(n, x=x, y=y)
    def add(u, v, L, hw="residential"):
        G.add_edge(u, v, key=0, length=L, highway=hw)
        G.add_edge(v, u, key=0, length=L, highway=hw)
    add(1, 2, 100)
    add(1, 3, 110); add(3, 4, 110); add(4, 2, 110, hw="footway")
    return G


class FakeDB:
    def __init__(self, unsafe): self.unsafe = unsafe
    def unsafe_edges(self, engine, hour, profile): return self.unsafe
    def edge_hazard(self, engine, hour):
        return pd.DataFrame([{"u": u, "v": v, "key": k, "depth_cm": 40} for u, v, k in self.unsafe])


class FakeDBByHour:
    """Different unsafe-edge set per forecast hour, for route_window tests."""
    def __init__(self, unsafe_by_hour: dict, depth_cm: int = 50):
        self.unsafe_by_hour = unsafe_by_hour
        self.depth_cm = depth_cm

    def unsafe_edges(self, engine, hour, profile):
        return self.unsafe_by_hour.get(hour, set())

    def edge_hazard(self, engine, hour):
        unsafe = self.unsafe_by_hour.get(hour, set())
        return pd.DataFrame([{"u": u, "v": v, "key": k, "depth_cm": self.depth_cm}
                             for u, v, k in unsafe])


def test_detour_when_direct_edge_unsafe(monkeypatch):
    G = square_graph()
    monkeypatch.setattr(routing, "db", FakeDB({(1, 2, 0), (2, 1, 0)}))
    R = routing.Router(G, engine=object())
    res = R.route((0, 0), (0, 0.001), "child", 0)
    assert res.nodes == [1, 3, 4, 2]
    assert res.length_m == 330 and res.baseline_length_m == 100
    assert res.baseline_blocked and res.max_depth_cm_on_route == 0


def test_vehicle_cannot_use_footway(monkeypatch):
    G = square_graph()
    monkeypatch.setattr(routing, "db", FakeDB({(1, 2, 0), (2, 1, 0)}))
    R = routing.Router(G, engine=object())
    # direct edge flooded, detour needs the footway D-B -> no vehicle route
    assert R.route((0, 0), (0, 0.001), "vehicle_small", 0) is None
    # but with nothing flooded the car takes the direct road
    monkeypatch.setattr(routing, "db", FakeDB(set()))
    assert R.route((0, 0), (0, 0.001), "vehicle_small", 0).nodes == [1, 2]


def test_route_window_finds_first_unsafe_hour(monkeypatch):
    """The direct edge (the 'usual' route) is safe hours 0-1, floods from
    hour 2 onward. route_window must report first_unsafe_hour=2, not 0."""
    G = square_graph()
    by_hour = {0: set(), 1: set(), 2: {(1, 2, 0), (2, 1, 0)}, 3: {(1, 2, 0), (2, 1, 0)}}
    monkeypatch.setattr(routing, "db", FakeDBByHour(by_hour, depth_cm=55))
    R = routing.Router(G, engine=object())
    win = R.route_window((0, 0), (0, 0.001), "child", range(4))
    assert win.first_unsafe_hour == 2
    assert win.max_depth_cm == 55
    assert win.baseline_length_m == 100


def test_route_window_never_unsafe(monkeypatch):
    G = square_graph()
    monkeypatch.setattr(routing, "db", FakeDBByHour({}))
    R = routing.Router(G, engine=object())
    win = R.route_window((0, 0), (0, 0.001), "adult", range(24))
    assert win.first_unsafe_hour is None
    assert win.baseline_length_m == 100


def test_highway_set_handles_list_valued_tags():
    """osmnx returns a list for `highway` when OSM tags a way with multiple
    values (e.g. a simplified edge merging a residential stretch and a
    driveway) instead of one string. edge_allowed must still work: a
    vehicle is blocked only if EVERY value in the list is non-drivable."""
    mixed = {"highway": ["residential", "footway"]}   # partly drivable
    all_non_drivable = {"highway": ["footway", "path"]}
    assert routing.edge_allowed("vehicle_small", mixed) is True
    assert routing.edge_allowed("vehicle_small", all_non_drivable) is False
    # pedestrian profiles are unaffected either way
    assert routing.edge_allowed("child", all_non_drivable) is True


def test_route_window_no_path_at_all(monkeypatch):
    """A profile with no possible path (flooding aside) is reported unsafe
    from hour 0, not silently treated as 'always safe'."""
    G = nx.MultiDiGraph(crs="EPSG:4326")
    G.add_node(1, x=0, y=0); G.add_node(2, x=1, y=1)  # disconnected
    monkeypatch.setattr(routing, "db", FakeDBByHour({}))
    R = routing.Router(G, engine=object())
    win = R.route_window((0, 0), (1, 1), "adult", range(24))
    assert win.first_unsafe_hour == 0
    assert win.baseline_length_m is None


def long_detour_graph():
    """A slow, long trip where the direct path's final leg only floods
    AFTER a traveler would already be en route -- built specifically to
    tell route() (checks hazard once, at departure) apart from
    route_time_aware() (checks hazard at each edge's own arrival hour).
    Leg lengths are chosen so an adult walker (config.WALK_SPEED_MPS =
    1.4 m/s) takes a bit over an hour to cover the direct path's first
    leg, crossing from forecast hour 0 into hour 1 before reaching the
    second leg."""
    G = nx.MultiDiGraph(crs="EPSG:4326")
    pts = {1: (0, 0), 2: (0.01, 0), 3: (0.011, 0), 4: (0, 0.01)}
    for n, (x, y) in pts.items():
        G.add_node(n, x=x, y=y)

    def add(u, v, L, hw="residential"):
        G.add_edge(u, v, key=0, length=L, highway=hw)
        G.add_edge(v, u, key=0, length=L, highway=hw)

    add(1, 2, 5400)   # direct leg 1: 1 -> 2, ~64 min at adult walking speed
    add(2, 3, 100)    # direct leg 2: 2 -> 3 (destination) -- floods at hour 1
    add(1, 4, 6000)   # detour leg 1: 1 -> 4, ~71 min -- always safe
    add(4, 3, 200)    # detour leg 2: 4 -> 3 (destination) -- always safe
    return G


def test_route_time_aware_avoids_hazard_that_appears_after_departure(monkeypatch):
    """The direct path (1->2->3) is shorter and looks completely safe at
    departure (hour 0), because its flooded leg (2->3) only floods
    starting hour 1 -- which is exactly when an adult walker would really
    reach it (leg 1 alone takes ~64 minutes). route() only checks hazard
    at the departure hour and gets this wrong, recommending the direct,
    actually-unsafe path. route_time_aware() must catch it and reroute via
    the always-safe detour -- this is the concrete case Stage 6's "hazard
    at departure hour only" simplification gets wrong."""
    G = long_detour_graph()
    by_hour = {0: set(), 1: {(2, 3, 0), (3, 2, 0)}}
    monkeypatch.setattr(routing, "db", FakeDBByHour(by_hour, depth_cm=60))
    R = routing.Router(G, engine=object())

    naive = R.route((0, 0), (0, 0.011), "adult", 0)
    assert naive.nodes == [1, 2, 3]      # fooled: picks the shorter, actually-unsafe path
    assert naive.length_m == 5500

    aware = R.route_time_aware((0, 0), (0, 0.011), "adult", 0)
    assert aware.nodes == [1, 4, 3]       # correctly reroutes via the detour
    assert aware.length_m == 6200
    assert aware.departure_hour == 0
    assert aware.arrival_hour == 1
    assert aware.hour_crossed is True
    assert aware.max_depth_cm_on_route == 0   # the detour it actually took was never flooded


def test_route_time_aware_matches_departure_hour_route_for_short_trips(monkeypatch):
    """A trip that never crosses an hour boundary should get the same
    answer from both methods -- time-awareness shouldn't change anything
    when it doesn't need to."""
    G = square_graph()
    monkeypatch.setattr(routing, "db", FakeDB(set()))
    R = routing.Router(G, engine=object())
    naive = R.route((0, 0), (0, 0.001), "adult", 0)
    aware = R.route_time_aware((0, 0), (0, 0.001), "adult", 0)
    assert aware.nodes == naive.nodes == [1, 2]
    assert aware.hour_crossed is False
    assert aware.arrival_hour == aware.departure_hour == 0


def test_route_time_aware_no_path_returns_none(monkeypatch):
    G = nx.MultiDiGraph(crs="EPSG:4326")
    G.add_node(1, x=0, y=0); G.add_node(2, x=1, y=1)  # disconnected
    monkeypatch.setattr(routing, "db", FakeDBByHour({}))
    R = routing.Router(G, engine=object())
    assert R.route_time_aware((0, 0), (1, 1), "adult", 0) is None


class StrictHourDB:
    """Raises if asked for hazard data at an hour beyond a real forecast's
    horizon -- proves route_time_aware() actually clamps elapsed-time hour
    lookups to config.MAX_HOUR instead of requesting hazard data for an
    hour the model never computed (which a real DB layer has no row for,
    and would silently come back empty -- masking a route as "safe" past
    the point the forecast says anything at all)."""
    def __init__(self, max_hour: int):
        self.max_hour = max_hour

    def unsafe_edges(self, engine, hour, profile):
        assert hour <= self.max_hour, f"requested hour {hour} beyond forecast horizon"
        return set()

    def edge_hazard(self, engine, hour):
        assert hour <= self.max_hour, f"requested hour {hour} beyond forecast horizon"
        return pd.DataFrame(columns=["u", "v", "key", "depth_cm"])


def test_route_time_aware_caps_hour_at_forecast_horizon(monkeypatch):
    from tidestep import config
    G = long_detour_graph()
    monkeypatch.setattr(routing, "db", StrictHourDB(config.MAX_HOUR))
    R = routing.Router(G, engine=object())
    # departing at the very last forecast hour, on a trip slow enough to
    # run past it, must clamp lookups to MAX_HOUR rather than ever asking
    # for MAX_HOUR + 1 (StrictHourDB would raise if it did)
    res = R.route_time_aware((0, 0), (0, 0.011), "adult", config.MAX_HOUR)
    assert res is not None
    assert res.departure_hour == config.MAX_HOUR
    assert res.arrival_hour == config.MAX_HOUR


def test_time_aware_route_geojson_shape(monkeypatch):
    G = square_graph()
    monkeypatch.setattr(routing, "db", FakeDB(set()))
    R = routing.Router(G, engine=object())
    res = R.route_time_aware((0, 0), (0, 0.001), "adult", 0)
    gj = R.time_aware_route_geojson(res)
    assert gj["type"] == "Feature"
    assert gj["geometry"]["type"] == "LineString"
    assert gj["properties"]["time_aware"] is True
    assert gj["properties"]["arrival_hour"] == 0
    assert gj["properties"]["hour_crossed"] is False


def test_route_advisory_reports_safe_and_unsafe_hours(monkeypatch):
    G = square_graph()
    by_hour = {0: set(), 1: set(), 2: {(1, 2, 0), (2, 1, 0)}, 3: {(1, 2, 0), (2, 1, 0)}}
    monkeypatch.setattr(routing, "db", FakeDBByHour(by_hour, depth_cm=45))
    R = routing.Router(G, engine=object())
    adv = R.route_advisory((0, 0), (0, 0.001), "child", range(4))
    assert adv.baseline_length_m == 100
    assert [h.safe for h in adv.hours] == [True, True, False, False]
    assert adv.hours[2].max_depth_cm == 45
    assert adv.hours[0].max_depth_cm == 0


def test_route_advisory_no_path_reports_every_hour_unsafe(monkeypatch):
    G = nx.MultiDiGraph(crs="EPSG:4326")
    G.add_node(1, x=0, y=0); G.add_node(2, x=1, y=1)  # disconnected
    monkeypatch.setattr(routing, "db", FakeDBByHour({}))
    R = routing.Router(G, engine=object())
    adv = R.route_advisory((0, 0), (1, 1), "adult", range(3))
    assert adv.baseline_length_m is None
    assert [h.safe for h in adv.hours] == [False, False, False]
