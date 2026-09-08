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
