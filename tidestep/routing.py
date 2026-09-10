"""Stage 6: flood-avoiding shortest path.

networkx Dijkstra over the osmnx graph with a per-request weight function:
an edge that contains any segment unsafe for the requested profile at the
requested forecast hour gets weight None (networkx treats that as "no
edge"). Edges whose highway type the profile cannot use are excluded the
same way. Everything else is weighted by length in metres.

``route()`` evaluates hazard once, at the departure hour, for the whole
trip -- fine for most trips at this bbox's scale, but a known simplification
(see docs/LIMITATIONS.md). ``route_time_aware()`` below removes that
simplification: it tracks elapsed travel time as the route is built and
checks each edge against the hazard state at the hour a traveler would
*actually* be crossing it, not the hour they left.

``route_advisory()`` reports, hour by hour across a forecast window,
whether one fixed (flood-blind) path is safe -- cheap, but blind to any
detour. ``route_best_departure()`` goes further: it recomputes the actual
best route (via route_time_aware()) at every hour, so it can find "there's
a safe way there right now, just longer" instead of reporting every hour
unsafe just because the *usual* path floods all day.

``route_multi_stop()`` chains route_time_aware() across an ordered list of
2+ waypoints, propagating elapsed time from each leg into the next leg's
departure hour, for trips with stops along the way. ``route_to_safety()``
answers a different kind of question from every method above -- not "get
me to THIS destination" but "get me to safety" -- by searching for the
nearest reachable point that stays flood-safe for the rest of the forecast
window, with no destination required.
"""
from __future__ import annotations

import heapq
from dataclasses import dataclass

import networkx as nx
import osmnx as ox

from . import config, db

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


@dataclass
class TimeAwareRouteResult:
    """Like RouteResult, but hazard was checked at each edge's own arrival
    hour (see Router.route_time_aware), not once at departure."""
    nodes: list[int]
    coords: list[tuple[float, float]]      # (lat, lon)
    length_m: float
    travel_time_min: float
    departure_hour: int
    arrival_hour: int          # forecast hour bucket when the traveler reaches the destination
    hour_crossed: bool         # True if the trip spans more than one forecast-hour bucket
    max_depth_cm_on_route: int  # each edge evaluated at ITS OWN arrival hour, not departure hour


@dataclass
class HourAdvisory:
    hour: int
    safe: bool
    max_depth_cm: int


@dataclass
class RouteAdvisory:
    """Hour-by-hour safe/unsafe forecast for one trip's usual (flood-blind)
    path across a whole window -- "when today is it safe to make this
    specific trip", for a rider planning ahead, as opposed to
    Router.route_window's single first-unsafe-hour (built for the alert
    loop's narrower "did it just become unsafe" question)."""
    baseline_length_m: float | None   # None only if no path exists at all (any profile)
    hours: list[HourAdvisory]


@dataclass
class HourRoute:
    """One hour's ACTUAL best route for route_best_departure -- not just
    whether the *usual* path is blocked (that's what HourAdvisory /
    RouteAdvisory answer above) but what the real shortest safe route is
    if a traveler left at this hour, which may detour around flooding the
    usual path hits."""
    hour: int
    safe: bool                       # True if any route exists departing this hour
    length_m: float | None
    travel_time_min: float | None
    max_depth_cm_on_route: int | None


@dataclass
class BestDeparturePlan:
    """Per-hour *actual* best routes for a trip, across a forecast window,
    plus the earliest hour a real route exists at all.

    This is deliberately a different, more expensive question than
    RouteAdvisory answers: RouteAdvisory fixes one flood-blind baseline
    path and checks it against each hour's hazard state, so if that one
    path is blocked at every hour it reports every hour unsafe -- even
    when a real (longer) detour would get a traveler there safely right
    now. BestDeparturePlan recomputes the actual best route for each hour
    (via route_time_aware), so "the usual way floods all day" and "there's
    no safe way to get there today" are no longer the same answer."""
    baseline_length_m: float | None        # flood-blind ideal length, for comparison only
    hours: list[HourRoute]
    recommended_hour: int | None           # earliest hour with a real route, or None
    recommended_length_m: float | None
    recommended_travel_time_min: float | None


@dataclass
class TripLeg:
    """One leg of a multi-stop trip -- the same information
    TimeAwareRouteResult carries for a single origin/destination pair,
    plus which waypoints in the request it connects."""
    origin_index: int          # index into the waypoints list this leg starts from
    destination_index: int
    nodes: list[int]
    coords: list[tuple[float, float]]      # (lat, lon)
    length_m: float
    travel_time_min: float
    departure_hour: int
    arrival_hour: int
    max_depth_cm_on_route: int


@dataclass
class TripPlan:
    """Result of Router.route_multi_stop: a chain of time-aware legs
    through an ordered list of 2+ waypoints, where each leg's departure
    hour is the PREVIOUS leg's actual arrival hour -- not the trip's
    overall departure hour repeated for every leg. See
    Router.route_multi_stop's docstring for why that distinction matters.

    If a leg has no safe route at the hour a traveler would actually
    start it, ``blocked_leg_index`` names the first such leg (0-based,
    into the waypoint pairs) and the trip-total fields are None; ``legs``
    still holds whatever legs were successfully computed before the
    block, so a caller can show "you can safely get this far."""
    legs: list[TripLeg]
    blocked_leg_index: int | None
    total_length_m: float | None
    total_travel_time_min: float | None
    departure_hour: int
    arrival_hour: int | None
    max_depth_cm_on_route: int         # deepest water crossed on any completed leg


@dataclass
class SafeHavenResult:
    """Nearest reachable point that stays safe for ``profile`` across the
    rest of the forecast window (departure_hour through the last modeled
    hour) -- an evacuation-style "where can I go that's safe" answer, as
    opposed to every other routing method here, which all require a
    specific destination the traveler already has in mind."""
    nodes: list[int]
    coords: list[tuple[float, float]]      # (lat, lon)
    length_m: float
    travel_time_min: float
    departure_hour: int
    arrival_hour: int
    max_depth_cm_on_route: int
    already_safe: bool    # True if the origin itself already qualifies (zero-length result)


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

    def route_advisory(self, origin: tuple[float, float], destination: tuple[float, float],
                       profile: str, hours: range) -> RouteAdvisory:
        """Full hour-by-hour safe/unsafe forecast for this trip's usual
        (flood-blind) path -- a "best time to leave" advisory for trip
        planning, as opposed to route_window's single first-unsafe-hour
        (built for the alert loop, which only cares about the moment a
        saved route first crosses into unsafe territory).

        Deliberately not built on top of route_window(): that method's
        contract (first_unsafe_hour only) is already relied on by
        scripts/hourly_update.py and covered by its own tests, and this
        method's "every hour, not just the first bad one" shape is
        different enough that sharing code would mean changing tested
        behavior to serve a second caller. Some loop duplication with
        route_window() below is the safer trade.
        """
        s, t = self.nearest(*origin, profile), self.nearest(*destination, profile)
        try:
            base_len, base_nodes = nx.single_source_dijkstra(
                self.G, s, t, weight=self._weight(profile, set()))
        except nx.NetworkXNoPath:
            return RouteAdvisory(baseline_length_m=None,
                                 hours=[HourAdvisory(hour=h, safe=False, max_depth_cm=0)
                                        for h in hours])

        base_edges = list(zip(base_nodes[:-1], base_nodes[1:]))
        out = []
        for h in hours:
            unsafe = db.unsafe_edges(self.engine, h, profile)
            depth = db.edge_hazard(self.engine, h)
            depth_by_edge = {(r.u, r.v, r.key): int(r.depth_cm) for r in depth.itertuples()}
            blocked = False
            max_depth = 0
            for u, v in base_edges:
                for k in self.G[u][v]:
                    if (u, v, k) in unsafe:
                        blocked = True
                    max_depth = max(max_depth, depth_by_edge.get((u, v, k), 0))
            out.append(HourAdvisory(hour=h, safe=not blocked, max_depth_cm=max_depth))
        return RouteAdvisory(baseline_length_m=float(base_len), hours=out)

    def _edge_time_s(self, attrs, profile: str) -> float:
        """Seconds a traveler of ``profile`` actually spends on this edge --
        what makes time-aware routing possible: without this we only know
        an edge's length, not when along the trip someone would be
        crossing it. Vehicles use the travel_time osmnx already derives
        from posted speed limits (tidestep/streets.py:
        ox.add_edge_travel_times); pedestrian profiles use a constant
        walking speed (config.WALK_SPEED_MPS)."""
        length = attrs.get("length", 1.0)
        if profile in VEHICLE_PROFILES:
            t = attrs.get("travel_time")
            if t is not None:
                return float(t)
            # osmnx couldn't assign a speed to this edge (rare, e.g. a
            # highway type missing from its speed table): fall back to a
            # conservative 30 km/h residential-street assumption rather
            # than crashing.
            return length / (30 / 3.6)
        return length / config.WALK_SPEED_MPS[profile]

    def route_time_aware(self, origin: tuple[float, float], destination: tuple[float, float],
                         profile: str, departure_hour: int) -> TimeAwareRouteResult | None:
        """Like route(), but hazard is checked at each edge's own arrival
        hour instead of once at the departure hour for the whole trip --
        retiring the MVP simplification stated in docs/PIPELINE.md Stage 6
        and docs/LIMITATIONS.md ("Hazard is evaluated at departure hour for
        the whole trip"). A route that looks clear when you leave can still
        be the wrong recommendation if a later segment floods by the time
        you'd actually reach it; this catches that instead of silently
        giving an unsafe answer.

        This can't reuse plain networkx Dijkstra with a fixed weight
        function: which hour's hazard applies to an edge depends on how
        much time has already elapsed on the path so far, which isn't
        available inside a per-edge weight callback. This is a small
        hand-rolled Dijkstra over (elapsed_seconds, node) instead, using
        _edge_time_s as the edge weight and re-deriving the current hour
        from accumulated time at each pop.
        """
        s, t = self.nearest(*origin, profile), self.nearest(*destination, profile)
        max_hour = config.MAX_HOUR
        unsafe_cache: dict[int, set[tuple]] = {}
        depth_cache: dict[int, dict[tuple, int]] = {}

        def unsafe_at(h: int) -> set[tuple]:
            if h not in unsafe_cache:
                unsafe_cache[h] = db.unsafe_edges(self.engine, h, profile)
            return unsafe_cache[h]

        def depth_at(h: int) -> dict[tuple, int]:
            if h not in depth_cache:
                df = db.edge_hazard(self.engine, h)
                depth_cache[h] = {(r.u, r.v, r.key): int(r.depth_cm) for r in df.itertuples()}
            return depth_cache[h]

        dist: dict[int, float] = {s: 0.0}
        prev: dict[int, tuple[int, int]] = {}   # node -> (prev_node, key of edge used)
        visited: set[int] = set()
        pq: list[tuple[float, int]] = [(0.0, s)]
        while pq:
            d, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)
            if u == t:
                break
            hour = min(departure_hour + int(d // 3600), max_hour)
            unsafe = unsafe_at(hour)
            for v, keydict in self.G[u].items():
                for k, attrs in keydict.items():
                    if (u, v, k) in unsafe or not edge_allowed(profile, attrs):
                        continue
                    nd = d + self._edge_time_s(attrs, profile)
                    if v not in dist or nd < dist[v]:
                        dist[v] = nd
                        prev[v] = (u, k)
                        heapq.heappush(pq, (nd, v))

        if t not in dist:
            return None

        # reconstruct the path from the prev pointers
        nodes = [t]
        keys: list[int] = []
        cur = t
        while cur != s:
            p, k = prev[cur]
            keys.append(k)
            nodes.append(p)
            cur = p
        nodes.reverse()
        keys.reverse()

        travel_time_s = dist[t]
        departure_hour = min(departure_hour, max_hour)
        arrival_hour = min(departure_hour + int(travel_time_s // 3600), max_hour)

        max_depth = 0
        elapsed = 0.0
        length_m = 0.0
        for u, v, k in zip(nodes[:-1], nodes[1:], keys):
            attrs = self.G[u][v][k]
            hour = min(departure_hour + int(elapsed // 3600), max_hour)
            max_depth = max(max_depth, depth_at(hour).get((u, v, k), 0))
            elapsed += self._edge_time_s(attrs, profile)
            length_m += attrs.get("length", 1.0)

        coords = [(self.G.nodes[n]["y"], self.G.nodes[n]["x"]) for n in nodes]
        return TimeAwareRouteResult(
            nodes=nodes, coords=coords, length_m=float(length_m),
            travel_time_min=round(travel_time_s / 60, 1),
            departure_hour=departure_hour, arrival_hour=arrival_hour,
            hour_crossed=arrival_hour != departure_hour,
            max_depth_cm_on_route=max_depth)

    def route_best_departure(self, origin: tuple[float, float], destination: tuple[float, float],
                             profile: str, hours: range) -> BestDeparturePlan:
        """Recompute the *actual* best route for every hour in ``hours``,
        not just whether one fixed path is blocked. route_advisory() above
        answers "is my usual way there safe at hour H" against a single
        flood-blind baseline path; this answers the more useful "what's
        the best way there at hour H" -- which can surface a real detour
        that keeps a trip possible at an hour route_advisory() would have
        to call unsafe, because it never looks past that one baseline
        path.

        Built on route_time_aware() (one full shortest-path search per
        candidate departure hour) rather than a from-scratch loop, so each
        hour's answer reuses the same engine and hazard-at-arrival-hour
        correctness route_time_aware() already has and is already tested
        for -- this method's job is only to run it across the window and
        summarize. That does mean up to len(hours) full searches per call
        (each of which may itself look up hazard data for several hours,
        as travel time advances the clock) -- fine at this bbox's scale,
        same tradeoff already accepted for route()/route_advisory() (see
        docs/LIMITATIONS.md's "full graph recompute per query" note).
        """
        s, t = self.nearest(*origin, profile), self.nearest(*destination, profile)
        try:
            base_len, _ = nx.single_source_dijkstra(
                self.G, s, t, weight=self._weight(profile, set()))
        except nx.NetworkXNoPath:
            base_len = None

        out: list[HourRoute] = []
        recommended_hour = None
        recommended_length_m = None
        recommended_travel_time_min = None
        for h in hours:
            res = self.route_time_aware(origin, destination, profile, h)
            if res is None:
                out.append(HourRoute(hour=h, safe=False, length_m=None,
                                     travel_time_min=None, max_depth_cm_on_route=None))
                continue
            out.append(HourRoute(hour=h, safe=True, length_m=res.length_m,
                                 travel_time_min=res.travel_time_min,
                                 max_depth_cm_on_route=res.max_depth_cm_on_route))
            if recommended_hour is None:
                recommended_hour = h
                recommended_length_m = res.length_m
                recommended_travel_time_min = res.travel_time_min
        return BestDeparturePlan(baseline_length_m=base_len, hours=out,
                                 recommended_hour=recommended_hour,
                                 recommended_length_m=recommended_length_m,
                                 recommended_travel_time_min=recommended_travel_time_min)

    def route_multi_stop(self, waypoints: list[tuple[float, float]], profile: str,
                         departure_hour: int) -> TripPlan:
        """Chain route_time_aware() across an ordered list of 2+ waypoints
        -- an origin, one or more stops, and a final destination -- where
        each leg's departure hour is the PREVIOUS leg's actual arrival
        hour, not the trip's overall departure hour repeated for every
        leg independently.

        That chaining is the point, not a implementation detail: checking
        each leg independently at the trip's starting hour is the exact
        "hazard at departure hour" mistake route_time_aware() itself was
        built to fix (see its docstring and docs/LIMITATIONS.md), just one
        level up. A later leg can look completely safe checked in
        isolation at hour 0, while the time actually spent on earlier legs
        means a traveler wouldn't reach it until an hour when it has
        already flooded --
        tests/test_routing.py::test_route_multi_stop_catches_a_leg_that_floods_by_the_time_you_reach_it
        constructs exactly that case and confirms route_multi_stop()
        catches it while checking each leg separately at hour 0 would not.

        Built entirely on route_time_aware() -- one full search per leg --
        so a multi-stop trip's correctness rests on the same
        already-tested arrival-hour logic as every other time-aware
        feature, rather than a new implementation of it.
        """
        if len(waypoints) < 2:
            raise ValueError("route_multi_stop needs at least 2 waypoints (origin + destination)")

        legs: list[TripLeg] = []
        hour = departure_hour
        max_depth = 0
        for i in range(len(waypoints) - 1):
            res = self.route_time_aware(waypoints[i], waypoints[i + 1], profile, hour)
            if res is None:
                return TripPlan(legs=legs, blocked_leg_index=i, total_length_m=None,
                                total_travel_time_min=None, departure_hour=departure_hour,
                                arrival_hour=None, max_depth_cm_on_route=max_depth)
            legs.append(TripLeg(
                origin_index=i, destination_index=i + 1, nodes=res.nodes, coords=res.coords,
                length_m=res.length_m, travel_time_min=res.travel_time_min,
                departure_hour=res.departure_hour, arrival_hour=res.arrival_hour,
                max_depth_cm_on_route=res.max_depth_cm_on_route))
            max_depth = max(max_depth, res.max_depth_cm_on_route)
            hour = res.arrival_hour    # next leg departs when THIS leg actually arrives

        return TripPlan(
            legs=legs, blocked_leg_index=None,
            total_length_m=sum(l.length_m for l in legs),
            total_travel_time_min=round(sum(l.travel_time_min for l in legs), 1),
            departure_hour=departure_hour, arrival_hour=legs[-1].arrival_hour,
            max_depth_cm_on_route=max_depth)

    def multi_stop_route_geojson(self, plan: TripPlan) -> dict:
        """A FeatureCollection with one Feature per completed leg (a
        LineString normally; a Point for the degenerate case where two
        consecutive waypoints snap to the same graph node, since a
        1-coordinate LineString is invalid GeoJSON -- the same fix
        safe_haven_geojson() applies for its zero-length "already safe"
        case). Trip-level totals (including blocked_leg_index, when a leg
        couldn't be completed) live in a top-level 'properties' key --
        not part of the core GeoJSON spec, but a widely-used, harmless
        extension that any spec-following consumer simply ignores."""
        features = []
        for leg in plan.legs:
            if len(leg.coords) < 2:
                lat, lon = leg.coords[0]
                geometry = {"type": "Point", "coordinates": [lon, lat]}
            else:
                geometry = {"type": "LineString",
                            "coordinates": [[lon, lat] for lat, lon in leg.coords]}
            features.append({
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "leg_index": leg.origin_index,
                    "length_m": round(leg.length_m, 1),
                    "travel_time_min": leg.travel_time_min,
                    "departure_hour": leg.departure_hour,
                    "arrival_hour": leg.arrival_hour,
                    "max_depth_cm_on_route": leg.max_depth_cm_on_route,
                },
            })
        return {
            "type": "FeatureCollection",
            "features": features,
            "properties": {
                "blocked_leg_index": plan.blocked_leg_index,
                "total_length_m": None if plan.total_length_m is None
                else round(plan.total_length_m, 1),
                "total_travel_time_min": plan.total_travel_time_min,
                "departure_hour": plan.departure_hour,
                "arrival_hour": plan.arrival_hour,
                "max_depth_cm_on_route": plan.max_depth_cm_on_route,
            },
        }

    def route_to_safety(self, origin: tuple[float, float], profile: str,
                        departure_hour: int) -> SafeHavenResult | None:
        """Find the nearest reachable point that stays safe for
        ``profile`` across the rest of the forecast window (departure_hour
        through the last modeled hour) -- an evacuation-style "where can I
        go that's safe" answer. Every other routing method in this file
        needs a specific destination the traveler already has in mind;
        this one only needs a starting point, which is the genuinely
        different question a real evacuation scenario asks.

        "Safe" here means "on a segment that db.always_safe_nodes()
        confirms stays flood-safe for this profile every hour from
        departure_hour onward" -- a conservative definition (a haven the
        traveler won't have to evacuate again from later the same day),
        not merely "safe this instant".

        Implemented as a multi-target version of route_time_aware()'s
        hand-rolled Dijkstra: instead of stopping at one fixed destination
        node, it stops at the FIRST node popped that is a known safe
        haven -- which, because Dijkstra pops nodes in increasing order of
        elapsed time, is guaranteed to be the nearest one in actual travel
        time, not just straight-line distance. Deliberately re-implements
        the search loop rather than generalizing route_time_aware() to
        take a target *set* -- route_time_aware() is already tested and
        relied on with its current single-target contract, and this
        method's "stop at any of many targets" shape is different enough
        that changing route_time_aware() to serve both would risk the
        already-proven one. Some loop duplication is the safer trade
        (the same tradeoff already made for route_advisory() vs.
        route_window() -- see route_advisory()'s docstring).
        """
        targets = db.always_safe_nodes(self.engine, range(departure_hour, config.MAX_HOUR + 1),
                                       profile)
        s = self.nearest(*origin, profile)
        if s in targets:
            y, x = self.G.nodes[s]["y"], self.G.nodes[s]["x"]
            return SafeHavenResult(nodes=[s], coords=[(y, x)], length_m=0.0, travel_time_min=0.0,
                                   departure_hour=departure_hour, arrival_hour=departure_hour,
                                   max_depth_cm_on_route=0, already_safe=True)
        if not targets:
            return None

        max_hour = config.MAX_HOUR
        unsafe_cache: dict[int, set[tuple]] = {}
        depth_cache: dict[int, dict[tuple, int]] = {}

        def unsafe_at(h: int) -> set[tuple]:
            if h not in unsafe_cache:
                unsafe_cache[h] = db.unsafe_edges(self.engine, h, profile)
            return unsafe_cache[h]

        def depth_at(h: int) -> dict[tuple, int]:
            if h not in depth_cache:
                df = db.edge_hazard(self.engine, h)
                depth_cache[h] = {(r.u, r.v, r.key): int(r.depth_cm) for r in df.itertuples()}
            return depth_cache[h]

        dist: dict[int, float] = {s: 0.0}
        prev: dict[int, tuple[int, int]] = {}
        visited: set[int] = set()
        pq: list[tuple[float, int]] = [(0.0, s)]
        found = None
        while pq:
            d, u = heapq.heappop(pq)
            if u in visited:
                continue
            visited.add(u)
            if u in targets:
                found = u
                break
            hour = min(departure_hour + int(d // 3600), max_hour)
            unsafe = unsafe_at(hour)
            for v, keydict in self.G[u].items():
                for k, attrs in keydict.items():
                    if (u, v, k) in unsafe or not edge_allowed(profile, attrs):
                        continue
                    nd = d + self._edge_time_s(attrs, profile)
                    if v not in dist or nd < dist[v]:
                        dist[v] = nd
                        prev[v] = (u, k)
                        heapq.heappush(pq, (nd, v))

        if found is None:
            return None

        nodes = [found]
        keys: list[int] = []
        cur = found
        while cur != s:
            p, k = prev[cur]
            keys.append(k)
            nodes.append(p)
            cur = p
        nodes.reverse()
        keys.reverse()

        travel_time_s = dist[found]
        arrival_hour = min(departure_hour + int(travel_time_s // 3600), max_hour)

        max_depth = 0
        elapsed = 0.0
        length_m = 0.0
        for u, v, k in zip(nodes[:-1], nodes[1:], keys):
            attrs = self.G[u][v][k]
            hour = min(departure_hour + int(elapsed // 3600), max_hour)
            max_depth = max(max_depth, depth_at(hour).get((u, v, k), 0))
            elapsed += self._edge_time_s(attrs, profile)
            length_m += attrs.get("length", 1.0)

        coords = [(self.G.nodes[n]["y"], self.G.nodes[n]["x"]) for n in nodes]
        return SafeHavenResult(
            nodes=nodes, coords=coords, length_m=float(length_m),
            travel_time_min=round(travel_time_s / 60, 1),
            departure_hour=departure_hour, arrival_hour=arrival_hour,
            max_depth_cm_on_route=max_depth, already_safe=False)

    def safe_haven_geojson(self, res: SafeHavenResult) -> dict:
        # a LineString needs at least two positions (GeoJSON spec); the
        # already-safe case is a single point (zero-length "route"), so
        # it gets a Point geometry instead rather than an invalid
        # one-coordinate LineString.
        if res.already_safe:
            lat, lon = res.coords[0]
            geometry = {"type": "Point", "coordinates": [lon, lat]}
        else:
            geometry = {"type": "LineString",
                        "coordinates": [[lon, lat] for lat, lon in res.coords]}
        return {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "length_m": round(res.length_m, 1),
                "travel_time_min": res.travel_time_min,
                "departure_hour": res.departure_hour,
                "arrival_hour": res.arrival_hour,
                "max_depth_cm_on_route": res.max_depth_cm_on_route,
                "already_safe": res.already_safe,
            },
        }

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

    def time_aware_route_geojson(self, res: TimeAwareRouteResult) -> dict:
        return {
            "type": "Feature",
            "geometry": {"type": "LineString",
                         "coordinates": [[lon, lat] for lat, lon in res.coords]},
            "properties": {
                "length_m": round(res.length_m, 1),
                "travel_time_min": res.travel_time_min,
                "departure_hour": res.departure_hour,
                "arrival_hour": res.arrival_hour,
                "hour_crossed": res.hour_crossed,
                "max_depth_cm_on_route": res.max_depth_cm_on_route,
                "time_aware": True,
            },
        }
