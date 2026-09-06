"""Stage 6: flood-avoiding shortest path.

networkx Dijkstra over the osmnx graph with a per-request weight function:
an edge that contains any segment unsafe for the requested profile at the
requested forecast hour gets weight None (networkx treats that as "no
edge"). Edges whose highway type the profile cannot use are excluded the
same way. Everything else is weighted by length in metres.

MVP simplification (stated in the write-up): hazard is evaluated at the
departure hour for the whole trip. A trip long enough to span an hour
boundary would need per-edge arrival-time hazard; that is a 2.0 item.
"""
from __future__ import annotations

from dataclasses import dataclass

import networkx as nx
import osmnx as ox

from . import db

# highway types each profile may use. Pedestrian profiles use everything
# (a sidewalk-less residential street is still walkable); vehicles skip
# footways/paths/steps/cycleways.
NON_DRIVABLE = {"footway", "path", "steps", "cycleway", "pedestrian",
                "bridleway", "corridor", "track"}
VEHICLE_PROFILES = {"vehicle_small", "vehicle_large", "vehicle_4wd"}


def _highway_set(data) -> set[str]:
    h = data.get("highway", "")
    if isinstance(h, (list, tuple)):
        return set(map(str, h))
    return {str(h)}


def edge_allowed(profile: str, data) -> bool:
    if profile in VEHICLE_PROFILES:
        return not _highway_set(data) <= NON_DRIVABLE
    return True


@dataclass
class RouteResult:
    nodes: list[int]
    coords: list[tuple[float, float]]      # (lat, lon)
    length_m: float
    avoided_edges: int                      # unsafe edges excluded from the graph
    baseline_length_m: float | None         # length ignoring flooding, for comparison
    baseline_blocked: bool                  # would the flood-blind route cross unsafe edges?
    max_depth_cm_on_route: int


class Router:
    def __init__(self, G: nx.MultiDiGraph, engine=None):
        self.G = G
        self.engine = engine or db.get_engine()
        # drivable subgraph so vehicle requests snap to a road node, not to a
        # nearby footpath node they could never leave
        drivable = [(u, v, k) for u, v, k, d in G.edges(keys=True, data=True)
                    if edge_allowed("vehicle_small", d)]
        self.G_drive = G.edge_subgraph(drivable).copy()

    def nearest(self, lat: float, lon: float, profile: str = "adult") -> int:
        g = self.G_drive if profile in VEHICLE_PROFILES else self.G
        return ox.nearest_nodes(g, lon, lat)

    def _weight(self, profile: str, unsafe: set[tuple]):
        def w(u, v, d):
            # d is {key: attrs} for a MultiDiGraph
            best = None
            for k, attrs in d.items():
                if (u, v, k) in unsafe or not edge_allowed(profile, attrs):
                    continue
                L = attrs.get("length", 1.0)
                best = L if best is None else min(best, L)
            return best
        return w

    def route(self, origin: tuple[float, float], destination: tuple[float, float],
              profile: str, forecast_hour: int) -> RouteResult | None:
        unsafe = db.unsafe_edges(self.engine, forecast_hour, profile)
        depth = db.edge_hazard(self.engine, forecast_hour)
        depth_by_edge = {(r.u, r.v, r.key): int(r.depth_cm) for r in depth.itertuples()}

        s, t = self.nearest(*origin, profile), self.nearest(*destination, profile)
        try:
            length, nodes = nx.single_source_dijkstra(
                self.G, s, t, weight=self._weight(profile, unsafe))
        except nx.NetworkXNoPath:
            return None

        # baseline: same profile, flooding ignored
        try:
            base_len, base_nodes = nx.single_source_dijkstra(
                self.G, s, t, weight=self._weight(profile, set()))
            base_blocked = any(
                any((u, v, k) in unsafe for k in self.G[u][v])
                for u, v in zip(base_nodes[:-1], base_nodes[1:]))
        except nx.NetworkXNoPath:
            base_len, base_blocked = None, False

        max_depth = 0
        for u, v in zip(nodes[:-1], nodes[1:]):
            for k in self.G[u][v]:
                max_depth = max(max_depth, depth_by_edge.get((u, v, k), 0))

        coords = [(self.G.nodes[n]["y"], self.G.nodes[n]["x"]) for n in nodes]
        return RouteResult(nodes=nodes, coords=coords, length_m=float(length),
                           avoided_edges=len(unsafe), baseline_length_m=base_len,
                           baseline_blocked=base_blocked,
                           max_depth_cm_on_route=max_depth)

    def route_geojson(self, res: RouteResult) -> dict:
        return {
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[lon, lat] for lat, lon in res.coords]},
            "properties": {
                "length_m": round(res.length_m, 1),
                "baseline_length_m": None if res.baseline_length_m is None
                else round(res.baseline_length_m, 1),
                "baseline_blocked": res.baseline_blocked,
                "avoided_edges": res.avoided_edges,
                "max_depth_cm_on_route": res.max_depth_cm_on_route,
            },
        }
