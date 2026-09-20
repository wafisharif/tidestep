"""Network resilience analysis: which streets are structural single points
of failure for the neighborhood's road network, and how many of those
ALSO flood at some point in the current 24h forecast.

Every other module in this app answers "point A to point B" questions --
route, best-departure, evacuate-to-safety, multi-stop. This module asks a
different, network-wide question that is exactly the kind of thing a
Congressional office cares about for infrastructure investment: not "can
I get through today" but "which specific streets, if they flood, cut part
of the neighborhood off from the rest -- not detoured, actually cut off,
because there is no other path at all -- and how much of this forecast do
they actually flood".

Definitions
-----------
A "chokepoint" is a graph edge (one physical street segment, possibly
split into several small pieces for the hazard model -- see
tidestep/segments.py) whose removal would disconnect the road network
into two pieces. This is the standard graph-theory notion of a *bridge*
edge, computed over the UNDIRECTED, profile-appropriate subgraph: a road
only counts as an alternate path if the profile being analyzed could
actually use it (see routing.edge_allowed), and direction doesn't matter
for "is there any other way around" -- a one-way street still provides a
physical path to walk or drive the other way in an emergency, matching
how routing.Router already treats direction (each two-way OSM street
becomes two directed graph edges; both collapse to the same undirected
connection here).

A naive "is (u, v) a bridge" check on the raw MultiDiGraph would wrongly
call almost every two-way street "redundant" (its own reverse-direction
copy looks like a second path between the same two nodes) and would
wrongly call a genuinely redundant PARALLEL street (e.g. a divided
highway modeled as two separate carriageways) a bridge if collapsed
carelessly the other way. ``_collapse_to_simple_graph`` below handles
both: it merges a physical segment's forward/backward directed pair into
ONE undirected edge (same OSM way, same ``key``), while keeping genuinely
distinct parallel ways (different ``key`` values between the same two
nodes) as separate, so real redundancy is never mistaken for a single
point of failure and vice versa.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import networkx as nx
from sqlalchemy import text

from . import config
from .routing import edge_allowed


@dataclass
class Chokepoint:
    """One structural single point of failure, with how much it actually
    floods in the currently-loaded forecast."""
    u: int
    v: int
    key: int
    nodes_isolated: int      # size of the smaller side if this segment is lost
    hours_unsafe: int        # of hours_total, how many this segment is unsafe for `profile`
    hours_total: int
    max_depth_cm: int        # deepest water this segment sees, any hour
    priority_score: int      # nodes_isolated * hours_unsafe -- see find_chokepoints doc


def _collapse_to_simple_graph(G: "nx.MultiDiGraph", profile: str) -> "nx.Graph":
    """Undirected simple graph for bridge-finding: one edge per unordered
    node pair that has at least one profile-usable physical segment, with
    ``keys`` recording exactly which OSM way(s) (graph edge keys) connect
    that pair -- ``len(keys) > 1`` means a genuinely redundant parallel
    street, not just the same street's other direction."""
    pair_keys: dict[frozenset, set] = defaultdict(set)
    for u, v, k, data in G.edges(keys=True, data=True):
        if u == v or not edge_allowed(profile, data):
            continue
        pair_keys[frozenset((u, v))].add(k)
    S = nx.Graph()
    for pair, keys in pair_keys.items():
        a, b = tuple(pair)
        S.add_edge(a, b, keys=keys)
    return S


def _chokepoint_topology(G: "nx.MultiDiGraph", profile: str) -> list[tuple[int, int, int, int]]:
    """[(u, v, key, nodes_isolated), ...] for every TRUE single point of
    failure -- a bridge edge in the collapsed simple graph that also has
    no redundant parallel way directly connecting the same two nodes.
    Pure topology; no hazard data involved yet.

    Runs in O(|V| + |E|) total, not O(bridges x (|V| + |E|)). An earlier
    version recomputed a fresh connected-component search -- preceded by
    a full ``S.copy()`` -- for every individual bridge, which is exactly
    right on the small hand-built graphs in tests/test_resilience.py (at
    most one or two bridges) but was never exercised at real scale until
    this project's real 55,577-segment Kings Point street graph was
    finally loaded end to end: that graph alone has 2,027 bridges (a
    normal suburban network has a lot of single-access/dead-end streets),
    so the old version did 2,027 full-graph copies plus 2,027 BFS
    traversals -- several minutes for one /api/network/chokepoints
    request. See docs/CHANGELOG.md for how this was found and measured.

    The fix uses the standard "bridge tree" construction: contracting
    every 2-edge-connected component (``nx.k_edge_components(S, k=2)`` --
    a maximal subgraph with no bridges inside it) down to a single point
    turns the whole graph into a TREE whose edges are exactly the
    bridges of S. Removing one tree edge splits the tree in two, and a
    single post-order pass over the tree (computing each subtree's total
    original-node weight) gives every bridge's isolation size in one
    linear-time traversal instead of one search per bridge. S can have
    more than one connected component (e.g. a profile-filtered subgraph
    that splits the map into separate islands), so this is a forest, not
    one tree -- handled by rooting a new traversal at every
    not-yet-visited node.
    """
    S = _collapse_to_simple_graph(G, profile)
    if S.number_of_nodes() == 0:
        return []

    components = list(nx.k_edge_components(S, k=2))
    comp_of: dict[int, int] = {}
    comp_size: list[int] = []
    for i, comp in enumerate(components):
        comp_size.append(len(comp))
        for n in comp:
            comp_of[n] = i

    # The bridge tree: one tree-node per 2-edge-connected component, one
    # tree-edge per bridge of S, tagged with the real (a, b) endpoints so
    # the parallel-way filter below can still look up S[a][b]["keys"].
    tree = nx.Graph()
    tree.add_nodes_from(range(len(components)))
    bridge_for_tree_edge: dict[frozenset, tuple[int, int]] = {}
    for a, b in nx.bridges(S):
        ca, cb = comp_of[a], comp_of[b]
        tree.add_edge(ca, cb)
        bridge_for_tree_edge[frozenset((ca, cb))] = (a, b)

    subtree_weight = list(comp_size)  # mutated in place during the walk below
    out = []
    visited = [False] * len(components)
    for start in range(len(components)):
        if visited[start]:
            continue
        # iterative post-order DFS over this tree component, so a subtree's
        # weight (sum of comp_size over every node under it) is known
        # before its parent needs it
        parent = {start: None}
        order = [start]
        stack = [start]
        visited[start] = True
        while stack:
            node = stack.pop()
            for nbr in tree[node]:
                if not visited[nbr]:
                    visited[nbr] = True
                    parent[nbr] = node
                    order.append(nbr)
                    stack.append(nbr)
        for node in reversed(order):
            p = parent[node]
            if p is not None:
                subtree_weight[p] += subtree_weight[node]
        # `start` is this forest-tree's root, so subtree_weight[start] is now
        # this LOCAL component's total node count -- the denominator every
        # bridge in this tree must split against. Using the whole graph's
        # node count here instead (as an earlier draft of this fix did)
        # would be wrong whenever S has more than one connected component:
        # a bridge can only isolate nodes that were reachable through it in
        # the first place, never nodes sitting in an entirely separate,
        # already-unreachable island elsewhere in the graph.
        local_total = subtree_weight[start]
        for node in order:
            p = parent[node]
            if p is None:
                continue
            a, b = bridge_for_tree_edge[frozenset((node, p))]
            keys = S[a][b]["keys"]
            if len(keys) != 1:
                continue  # a parallel way survives losing this one -- not a chokepoint
            isolated = min(subtree_weight[node], local_total - subtree_weight[node])
            out.append((a, b, next(iter(keys)), isolated))
    return out


def find_chokepoints(G: "nx.MultiDiGraph", engine, profile: str = "adult") -> list[Chokepoint]:
    """Every structural single point of failure for ``profile``'s usable
    street network, ranked by ``priority_score`` (nodes isolated if it's
    lost, times how many forecast hours it's actually unsafe) -- streets
    that are BOTH structurally irreplaceable AND actually flood this
    forecast rank above ones that are only one or the other. A chokepoint
    with ``hours_unsafe == 0`` is still a real single point of failure
    (worth knowing for planning) that simply doesn't flood in the
    currently-loaded forecast.

    Deliberately reuses ``routing.edge_allowed`` for the same
    profile-usability rule the router itself applies, so "is this an
    alternate path" always means the same thing here as it does when
    actually routing someone -- the two questions ("can this profile get
    through" and "is this street structurally replaceable for this
    profile") share one definition of the graph, not two that could
    quietly drift apart.
    """
    if profile not in config.DEPTH_LIMIT_M:
        raise ValueError(f"unknown profile {profile!r}")
    topology = _chokepoint_topology(G, profile)
    if not topology:
        return []
    col = f"safe_{profile}"
    hours_total = config.FORECAST_HOURS
    results = []
    with engine.connect() as conn:
        for u, v, key, isolated in topology:
            row = conn.execute(text(f"""
                SELECT COALESCE(MAX(h.depth_cm), 0) AS max_depth_cm,
                       COUNT(DISTINCT h.forecast_hour) FILTER (WHERE NOT h.{col}) AS hours_unsafe
                FROM segments s JOIN hazard h USING (segment_id)
                WHERE h.scenario_cm = 0
                  AND ((s.u = :u AND s.v = :v AND s.key = :k)
                    OR (s.u = :v AND s.v = :u AND s.key = :k))
            """), {"u": u, "v": v, "k": key}).one()
            hours_unsafe = int(row.hours_unsafe or 0)
            results.append(Chokepoint(
                u=u, v=v, key=key, nodes_isolated=isolated,
                hours_unsafe=hours_unsafe, hours_total=hours_total,
                max_depth_cm=int(row.max_depth_cm or 0),
                priority_score=isolated * hours_unsafe,
            ))
    results.sort(key=lambda c: (c.priority_score, c.hours_unsafe, c.nodes_isolated), reverse=True)
    return results


def chokepoints_geojson(G: "nx.MultiDiGraph", engine, profile: str = "adult") -> dict:
    """``find_chokepoints`` as a GeoJSON FeatureCollection (one Feature per
    chokepoint, a MultiLineString of that edge's constituent hazard-model
    segments -- see tidestep/segments.py's note on splitting a graph edge
    into several pieces), ready for the frontend/iOS map layer."""
    chokepoints = find_chokepoints(G, engine, profile)
    features = []
    with engine.connect() as conn:
        for c in chokepoints:
            geom = conn.execute(text("""
                SELECT ST_AsGeoJSON(ST_Collect(s.geom), 6)::json AS geometry
                FROM segments s
                WHERE (s.u = :u AND s.v = :v AND s.key = :k)
                   OR (s.u = :v AND s.v = :u AND s.key = :k)
            """), {"u": c.u, "v": c.v, "k": c.key}).scalar_one()
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "u": c.u, "v": c.v, "key": c.key,
                    "nodes_isolated": c.nodes_isolated,
                    "hours_unsafe": c.hours_unsafe, "hours_total": c.hours_total,
                    "max_depth_cm": c.max_depth_cm,
                    "priority_score": c.priority_score,
                },
            })
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {"profile": profile, "chokepoint_count": len(chokepoints)},
    }
