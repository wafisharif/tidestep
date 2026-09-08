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


@dataclass
class WindowResult:
    """Result of checking a saved route across a range of forecast hours,
    not just "now". This is what lets an alert say *when* a route floods
    instead of only whether it is flooded at this instant."""
    first_unsafe_hour: int | None    # None = safe for the whole window checked
    max_depth_cm: int                # deepest water the flood-blind route hits, any hour
    baseline_length_m: float | None  # None only if no path exists at all (any profile)


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

    def route_window(self, origin: tuple[float, float], destination: tuple[float, float],
                     profile: str, hours: range) -> WindowResult:
        """Check a route across every hour in ``hours`` (not just one), so a
        saved-route alert can say *when* flooding starts instead of only
        whether it is flooded right now.

        The "usual" (flood-blind) route is fixed for a given profile — it
        does not change hour to hour, only whether it is passable does — so
        it is computed once and re-checked against each hour's unsafe set.
        """
        s, t = self.nearest(*origin, profile), self.nearest(*destination, profile)
        try:
            base_len, base_nodes = nx.single_source_dijkstra(
                self.G, s, t, weight=self._weight(profile, set()))
        except nx.NetworkXNoPath:
            # no route exists for this profile at all, flooding aside —
            # e.g. a vehicle profile with no drivable path between the
            # points. Every hour is "unsafe" in the sense that there is no
            # way to make the trip.
            return WindowResult(first_unsafe_hour=0, max_depth_cm=0, baseline_length_m=None)

        base_edges = list(zip(base_nodes[:-1], base_nodes[1:]))
        first_unsafe = None
        max_depth = 0
        for h in hours:
            unsafe = db.unsafe_edges(self.engine, h, profile)
            depth = db.edge_hazard(self.engine, h)
            depth_by_edge = {(r.u, r.v, r.key): int(r.depth_cm) for r in depth.itertuples()}
            blocked = False
            for u, v in base_edges:
                for k in self.G[u][v]:
                    if (u, v, k) in unsafe:
                        blocked = True
                    max_depth = max(max_depth, depth_by_edge.get((u, v, k), 0))
            if blocked and first_unsafe is None:
                first_unsafe = h
        return WindowResult(first_unsafe_hour=first_unsafe, max_depth_cm=max_depth,
                            baseline_length_m=float(base_len))

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
