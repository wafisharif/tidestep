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
