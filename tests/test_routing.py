"""Router on a tiny hand-built graph; DB calls are stubbed."""
import networkx as nx
import pandas as pd
import pytest

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


class FakeDBWithHavens:
    """Like FakeDBByHour, plus always_safe_nodes(...) for route_to_safety
    tests -- a fixed set of node ids treated as safe havens regardless of
    hour (tests that need per-hour haven variation can subclass)."""
    def __init__(self, unsafe_by_hour: dict, safe_nodes: set, depth_cm: int = 50):
        self.unsafe_by_hour = unsafe_by_hour
        self.safe_nodes = safe_nodes
        self.depth_cm = depth_cm

    def unsafe_edges(self, engine, hour, profile):
        return self.unsafe_by_hour.get(hour, set())

    def edge_hazard(self, engine, hour):
        unsafe = self.unsafe_by_hour.get(hour, set())
        return pd.DataFrame([{"u": u, "v": v, "key": k, "depth_cm": self.depth_cm}
                             for u, v, k in unsafe])

    def always_safe_nodes(self, engine, hours, profile):
        return self.safe_nodes


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


def test_route_best_departure_finds_a_safe_detour_advisory_would_call_unsafe(monkeypatch):
    """route_advisory() checks only the fixed 'usual' (flood-blind
    baseline) path -- the direct edge -- against each hour's hazard state,
    so if the direct edge is unsafe at every hour it reports every hour as
    unsafe, even though a real detour exists. route_best_departure()
    actually recomputes the best route at each hour (via
    route_time_aware()), so it correctly finds the detour is safe right
    away -- concrete proof this answers a genuinely different, more useful
    question than route_advisory()."""
    G = square_graph()
    monkeypatch.setattr(routing, "db", FakeDB({(1, 2, 0), (2, 1, 0)}))
    R = routing.Router(G, engine=object())

    adv = R.route_advisory((0, 0), (0, 0.001), "child", range(3))
    assert all(h.safe is False for h in adv.hours)   # baseline-only view: looks hopeless

    plan = R.route_best_departure((0, 0), (0, 0.001), "child", range(3))
    assert plan.recommended_hour == 0                 # but a detour is safe right away
    assert plan.recommended_length_m == 330
    assert plan.baseline_length_m == 100               # ideal flood-blind length, for comparison
    assert all(h.safe for h in plan.hours)
    assert all(h.length_m == 330 for h in plan.hours)
    assert all(h.max_depth_cm_on_route == 0 for h in plan.hours)  # detour never floods


def test_route_best_departure_picks_earliest_safe_hour(monkeypatch):
    """When the direct edge is only blocked for the first two hours,
    recommended_hour should be 0 (a safe detour exists right away, even
    though the direct path only clears up at hour 2), and each hour's
    reported length should reflect what actually was best THAT hour: the
    longer detour while blocked, the short direct edge once it clears."""
    G = square_graph()
    by_hour = {0: {(1, 2, 0), (2, 1, 0)}, 1: {(1, 2, 0), (2, 1, 0)}, 2: set(), 3: set()}
    monkeypatch.setattr(routing, "db", FakeDBByHour(by_hour, depth_cm=50))
    R = routing.Router(G, engine=object())
    plan = R.route_best_departure((0, 0), (0, 0.001), "child", range(4))
    assert [h.safe for h in plan.hours] == [True, True, True, True]
    assert plan.hours[0].length_m == 330 and plan.hours[1].length_m == 330
    assert plan.hours[2].length_m == 100 and plan.hours[3].length_m == 100
    assert plan.recommended_hour == 0
    assert plan.recommended_length_m == 330


def test_route_best_departure_no_path_returns_none_recommended(monkeypatch):
    G = nx.MultiDiGraph(crs="EPSG:4326")
    G.add_node(1, x=0, y=0); G.add_node(2, x=1, y=1)  # disconnected
    monkeypatch.setattr(routing, "db", FakeDBByHour({}))
    R = routing.Router(G, engine=object())
    plan = R.route_best_departure((0, 0), (1, 1), "adult", range(3))
    assert plan.recommended_hour is None
    assert plan.recommended_length_m is None
    assert plan.recommended_travel_time_min is None
    assert plan.baseline_length_m is None
    assert all(h.safe is False for h in plan.hours)


def test_route_best_departure_respects_hour_clamp_at_forecast_horizon(monkeypatch):
    """Same clamping guarantee as route_time_aware() (StrictHourDB raises
    if asked for hazard past the forecast horizon), exercised through
    route_best_departure()'s per-hour loop rather than a single call."""
    from tidestep import config
    G = long_detour_graph()
    monkeypatch.setattr(routing, "db", StrictHourDB(config.MAX_HOUR))
    R = routing.Router(G, engine=object())
    plan = R.route_best_departure((0, 0), (0, 0.011), "adult",
                                  range(config.MAX_HOUR, config.MAX_HOUR + 1))
    assert plan.hours[0].safe is True
    assert plan.recommended_hour == config.MAX_HOUR


# --- route_multi_stop ---------------------------------------------------

def three_stop_graph():
    """Extends long_detour_graph with a third stop: reaching stop 3 takes
    long enough (leg 1, ~65.5 min at adult walking speed) to cross from
    forecast hour 0 into hour 1, and the final leg (3->5) only floods
    starting hour 1 -- exactly the hour a traveler chaining from leg 1
    would really start it, even though it looks completely safe if
    (wrongly) checked at the trip's overall hour-0 departure time."""
    G = nx.MultiDiGraph(crs="EPSG:4326")
    pts = {1: (0, 0), 2: (0.01, 0), 3: (0.011, 0), 5: (0.012, 0)}
    for n, (x, y) in pts.items():
        G.add_node(n, x=x, y=y)

    def add(u, v, L, hw="residential"):
        G.add_edge(u, v, key=0, length=L, highway=hw)
        G.add_edge(v, u, key=0, length=L, highway=hw)

    add(1, 2, 5400)   # leg 1a: ~64 min
    add(2, 3, 100)    # leg 1b: leg 1 (1->3) totals ~65.5 min, crosses into hour 1
    add(3, 5, 100)    # leg 2 (3->5): floods starting hour 1
    return G


def test_route_multi_stop_catches_a_leg_that_floods_by_the_time_you_reach_it(monkeypatch):
    """Checking each leg independently at the trip's overall hour-0
    departure would say both legs are safe (leg 2, 3->5, isn't flooded at
    hour 0). But leg 1 alone takes over an hour, so a traveler wouldn't
    actually reach stop 3 -- and start leg 2 -- until hour 1, by which
    time leg 2 HAS flooded. route_multi_stop() must catch this by
    chaining each leg's departure hour from the previous leg's real
    arrival hour, not repeating the trip's overall departure hour."""
    G = three_stop_graph()
    by_hour = {0: set(), 1: {(3, 5, 0), (5, 3, 0)}}
    monkeypatch.setattr(routing, "db", FakeDBByHour(by_hour, depth_cm=70))
    R = routing.Router(G, engine=object())

    waypoints = [(0, 0), (0, 0.011), (0, 0.012)]   # origin -> stop 3 -> destination 5
    plan = R.route_multi_stop(waypoints, "adult", 0)

    assert len(plan.legs) == 1                  # leg 1 (origin -> stop) succeeded
    assert plan.legs[0].destination_index == 1
    assert plan.legs[0].departure_hour == 0
    assert plan.legs[0].arrival_hour == 1       # crossed into hour 1, as designed
    assert plan.blocked_leg_index == 1          # leg 2 (stop -> destination) is where it fails
    assert plan.total_length_m is None
    assert plan.arrival_hour is None

    # sanity check: checking leg 2 in isolation at hour 0 (the naive, wrong
    # way) WOULD say it's safe -- proving this is a real trap, not an
    # unreachable edge case
    naive_leg2 = R.route_time_aware((0, 0.011), (0, 0.012), "adult", 0)
    assert naive_leg2 is not None


def test_route_multi_stop_all_legs_succeed(monkeypatch):
    G = square_graph()
    monkeypatch.setattr(routing, "db", FakeDB(set()))
    R = routing.Router(G, engine=object())
    plan = R.route_multi_stop([(0, 0), (0, 0.001), (0, 0)], "adult", 0)
    assert plan.blocked_leg_index is None
    assert len(plan.legs) == 2
    assert plan.total_length_m == 200
    assert plan.departure_hour == 0
    assert plan.arrival_hour == 0    # short trip, never crosses an hour boundary

    gj = R.multi_stop_route_geojson(plan)
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) == 2
    assert gj["properties"]["total_length_m"] == 200
    assert gj["properties"]["blocked_leg_index"] is None


def test_multi_stop_route_geojson_partial_when_blocked(monkeypatch):
    G = three_stop_graph()
    by_hour = {0: set(), 1: {(3, 5, 0), (5, 3, 0)}}
    monkeypatch.setattr(routing, "db", FakeDBByHour(by_hour, depth_cm=70))
    R = routing.Router(G, engine=object())
    plan = R.route_multi_stop([(0, 0), (0, 0.011), (0, 0.012)], "adult", 0)
    gj = R.multi_stop_route_geojson(plan)
    assert gj["type"] == "FeatureCollection"
    assert len(gj["features"]) == 1     # only leg 1 completed
    assert gj["properties"]["blocked_leg_index"] == 1
    assert gj["properties"]["total_length_m"] is None


def test_multi_stop_route_geojson_uses_point_when_a_leg_is_zero_length(monkeypatch):
    """Two consecutive waypoints that snap to the same graph node produce
    a zero-length leg -- its GeoJSON Feature must be a Point, not an
    invalid single-coordinate LineString (found via live testing against
    the real synthetic scenario, where two requested waypoints happened
    to snap to the same street node -- the same fix already applied to
    safe_haven_geojson()'s zero-length 'already safe' case)."""
    G = square_graph()
    monkeypatch.setattr(routing, "db", FakeDB(set()))
    R = routing.Router(G, engine=object())
    plan = R.route_multi_stop([(0, 0), (0, 0), (0, 0.001)], "adult", 0)
    assert plan.blocked_leg_index is None
    assert plan.legs[0].length_m == 0.0
    gj = R.multi_stop_route_geojson(plan)
    assert gj["features"][0]["geometry"]["type"] == "Point"
    assert gj["features"][1]["geometry"]["type"] == "LineString"


def test_route_multi_stop_requires_at_least_two_waypoints(monkeypatch):
    G = square_graph()
    monkeypatch.setattr(routing, "db", FakeDB(set()))
    R = routing.Router(G, engine=object())
    with pytest.raises(ValueError):
        R.route_multi_stop([(0, 0)], "adult", 0)


# --- route_to_safety ------------------------------------------------------

def test_route_to_safety_finds_nearest_haven(monkeypatch):
    G = square_graph()
    monkeypatch.setattr(routing, "db", FakeDBWithHavens({}, safe_nodes={4}))
    R = routing.Router(G, engine=object())
    res = R.route_to_safety((0, 0), "adult", 0)
    assert res is not None
    assert res.already_safe is False
    # shortest path to node 4: 1->2 (100m, direct) then the 2->4 footway
    # (110m) -- adult profiles can use footways, so this beats 1->3->4
    # (110+110=220m) even though it's not the "road" route
    assert res.nodes == [1, 2, 4]
    assert res.length_m == 210

    gj = R.safe_haven_geojson(res)
    assert gj["geometry"]["type"] == "LineString"
    assert gj["properties"]["already_safe"] is False


def test_route_to_safety_already_safe_origin(monkeypatch):
    """If the origin itself snaps to a node that already qualifies as a
    haven, the result is a zero-length 'you're already safe' answer, not
    a search for somewhere else to go."""
    G = square_graph()
    monkeypatch.setattr(routing, "db", FakeDBWithHavens({}, safe_nodes={1}))
    R = routing.Router(G, engine=object())
    res = R.route_to_safety((0, 0), "adult", 0)
    assert res.already_safe is True
    assert res.nodes == [1]
    assert res.length_m == 0.0
    assert res.arrival_hour == 0

    gj = R.safe_haven_geojson(res)
    assert gj["geometry"]["type"] == "Point"    # not an invalid 1-point LineString


def test_route_to_safety_no_havens_at_all(monkeypatch):
    G = square_graph()
    monkeypatch.setattr(routing, "db", FakeDBWithHavens({}, safe_nodes=set()))
    R = routing.Router(G, engine=object())
    assert R.route_to_safety((0, 0), "adult", 0) is None


def test_route_to_safety_returns_none_if_haven_unreachable(monkeypatch):
    G = nx.MultiDiGraph(crs="EPSG:4326")
    G.add_node(1, x=0, y=0); G.add_node(2, x=1, y=1)  # disconnected, no edges
    monkeypatch.setattr(routing, "db", FakeDBWithHavens({}, safe_nodes={2}))
    R = routing.Router(G, engine=object())
    assert R.route_to_safety((0, 0), "adult", 0) is None


def test_route_to_safety_queries_havens_from_departure_hour_onward(monkeypatch):
    """always_safe_nodes() must be asked about the window starting at the
    REQUESTED departure hour through the forecast horizon -- not the
    whole 0..MAX_HOUR range, which would incorrectly disqualify a node
    that only floods earlier in the day, before this traveler would even
    be leaving."""
    from tidestep import config

    class RecordingHavenDB(FakeDBWithHavens):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.calls = []

        def always_safe_nodes(self, engine, hours, profile):
            self.calls.append((list(hours), profile))
            return super().always_safe_nodes(engine, hours, profile)

    G = square_graph()
    spy = RecordingHavenDB({}, safe_nodes={4})
    monkeypatch.setattr(routing, "db", spy)
    R = routing.Router(G, engine=object())
    R.route_to_safety((0, 0), "adult", 5)
    assert spy.calls == [(list(range(5, config.MAX_HOUR + 1)), "adult")]


def chain_graph():
    """Four nodes in a straight line (1-2-3-4, 100 m per leg), used to
    build a multi-stop trip where the GIVEN waypoint order forces
    backtracking but a reordering of the intermediate stops does not --
    the exact case route_multi_stop_optimized() exists to catch."""
    G = nx.MultiDiGraph(crs="EPSG:4326")
    pts = {1: (0, 0), 2: (0.001, 0), 3: (0.002, 0), 4: (0.003, 0)}
    for n, (x, y) in pts.items():
        G.add_node(n, x=x, y=y)

    def add(u, v, L, hw="residential"):
        G.add_edge(u, v, key=0, length=L, highway=hw)
        G.add_edge(v, u, key=0, length=L, highway=hw)

    add(1, 2, 100); add(2, 3, 100); add(3, 4, 100)
    return G


def test_route_multi_stop_optimized_finds_a_shorter_visiting_order(monkeypatch):
    """Waypoints as given: origin (node 1), then the FAR intermediate stop
    (node 3), then the NEAR one (node 2), then destination (node 4) --
    forces backtracking: 1->3 (200m) -> 2 (100m, backward) -> 4 (200m) =
    500m total. Visiting the near stop first instead (1->2->3->4) needs no
    backtracking at all: 300m. The optimizer must find that reordering."""
    G = chain_graph()
    monkeypatch.setattr(routing, "db", FakeDB(set()))
    R = routing.Router(G, engine=object())
    waypoints = [(0, 0), (0, 0.002), (0, 0.001), (0, 0.003)]

    naive = R.route_multi_stop(waypoints, "adult", 0)
    assert naive.total_length_m == 500

    opt = R.route_multi_stop_optimized(waypoints, "adult", 0)
    assert opt.optimized is True
    assert opt.order == [0, 2, 1, 3]           # visit the near stop (index 2) first
    assert opt.plan.total_length_m == 300      # no backtracking
    assert opt.orders_tried == 2               # 2! permutations of 2 intermediate stops
    assert opt.orders_complete == 2            # both orders are fully safe here


def test_route_multi_stop_optimized_no_reorder_needed_for_one_stop(monkeypatch):
    G = chain_graph()
    monkeypatch.setattr(routing, "db", FakeDB(set()))
    R = routing.Router(G, engine=object())
    waypoints = [(0, 0), (0, 0.001), (0, 0.003)]   # only one intermediate stop
    opt = R.route_multi_stop_optimized(waypoints, "adult", 0)
    assert opt.optimized is False
    assert opt.order == [0, 1, 2]
    assert opt.orders_tried == 1
    assert opt.orders_complete == 1


def test_route_multi_stop_optimized_rejects_too_many_stops():
    G = chain_graph()
    R = routing.Router(G, engine=object())
    waypoints = [(0, 0)] * 9   # 7 intermediate stops, one over MAX_OPTIMIZE_STOPS
    with pytest.raises(ValueError):
        R.route_multi_stop_optimized(waypoints, "adult", 0)


def test_route_multi_stop_optimized_falls_back_to_given_order_when_nothing_completes(monkeypatch):
    """The only edge out of the origin (1-2) is unsafe at every hour, so
    every possible visiting order is blocked on its very first leg --
    there is no "best" order to find. optimize_order must not silently
    return a blocked plan pretending it's the answer; it falls back to
    the plan for the order the caller actually gave."""
    G = chain_graph()
    monkeypatch.setattr(routing, "db", FakeDB({(1, 2, 0), (2, 1, 0)}))
    R = routing.Router(G, engine=object())
    waypoints = [(0, 0), (0, 0.002), (0, 0.001), (0, 0.003)]
    opt = R.route_multi_stop_optimized(waypoints, "adult", 0)
    assert opt.optimized is False
    assert opt.order == [0, 1, 2, 3]           # unchanged: the given order
    assert opt.orders_complete == 0
    assert opt.orders_tried == 2
    assert opt.plan.blocked_leg_index == 0
