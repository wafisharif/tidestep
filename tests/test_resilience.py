"""Network-wide chokepoint (single point of failure) analysis on small
hand-built graphs -- pure topology, no database involved (find_chokepoints()
and chokepoints_geojson() additionally hit the hazard table for per-edge
flood stats; that half is covered against real loaded data in
tests/test_integration.py, alongside the dev_seed grid's own real vehicle
chokepoint -- see test_integration.py's docstring for that case)."""
import time

import networkx as nx
import pytest

from tidestep import resilience


def two_way_edge(G, u, v, key=0, highway="residential"):
    """Mirrors how osmnx actually represents a two-way street: two
    directed edges, same key, one each direction -- NOT two independent
    physical connections. _collapse_to_simple_graph must merge these back
    into one undirected edge, not mistake the reverse copy for a second,
    redundant path (the exact mistake a naive multigraph bridge check
    would make)."""
    G.add_edge(u, v, key=key, highway=highway)
    G.add_edge(v, u, key=key, highway=highway)


def dumbbell_graph():
    """Two triangles (1-2-3 and 4-5-6), connected only by a single
    two-way street 3-4 -- the textbook single point of failure: lose 3-4
    and the two triangles fall apart into two separate components of
    equal size (3 nodes each)."""
    G = nx.MultiDiGraph()
    for n in range(1, 7):
        G.add_node(n, x=0.0, y=0.0)
    for u, v in [(1, 2), (2, 3), (3, 1), (4, 5), (5, 6), (6, 4)]:
        two_way_edge(G, u, v)
    two_way_edge(G, 3, 4)
    return G


def test_two_way_street_correctly_collapses_to_one_edge_not_two():
    """A naive multigraph bridge check would see the reverse-direction
    copy of the same physical street as a second path and wrongly call
    3-4 'not a bridge'. Confirms the collapse merges same-key forward and
    backward edges into exactly one undirected connection."""
    G = dumbbell_graph()
    S = resilience._collapse_to_simple_graph(G, "adult")
    assert S.number_of_edges() == 7   # 3 + 3 triangle edges + the one bridge
    assert set(S[3][4]["keys"]) == {0}


def test_finds_the_single_bridge_and_its_isolation_size():
    G = dumbbell_graph()
    topo = resilience._chokepoint_topology(G, "adult")
    assert len(topo) == 1
    u, v, key, isolated = topo[0]
    assert {u, v} == {3, 4}
    assert key == 0
    assert isolated == 3   # both sides are exactly 3 nodes


def test_genuinely_parallel_street_is_not_a_chokepoint():
    """Two DIFFERENT physical ways (different keys) directly connecting
    the same two nodes -- e.g. a divided road's two carriageways -- means
    losing either one still leaves the other. Must not be flagged."""
    G = dumbbell_graph()
    two_way_edge(G, 3, 4, key=1)   # a second, parallel way between 3 and 4
    assert resilience._chokepoint_topology(G, "adult") == []


def test_vehicle_profile_ignores_footway_only_connections():
    """A footway is not usable by a vehicle profile, so it must not count
    as an alternate path for that profile's topology, even though
    pedestrians (child/adult) can use it to make the connection genuinely
    redundant."""
    G = dumbbell_graph()
    two_way_edge(G, 3, 4, key=1, highway="footway")
    # adult (pedestrian): footway makes this genuinely redundant now
    assert resilience._chokepoint_topology(G, "adult") == []
    # vehicle_small: footway doesn't count, so the road edge is still the only real connection
    topo = resilience._chokepoint_topology(G, "vehicle_small")
    assert len(topo) == 1
    u, v, key, isolated = topo[0]
    assert {u, v} == {3, 4} and key == 0 and isolated == 3


def test_no_chokepoints_in_a_fully_connected_ring():
    """A simple ring has no bridges at all -- every edge has an alternate
    path the long way around."""
    G = nx.MultiDiGraph()
    for n in range(1, 6):
        G.add_node(n, x=0.0, y=0.0)
    for u, v in [(1, 2), (2, 3), (3, 4), (4, 5), (5, 1)]:
        two_way_edge(G, u, v)
    assert resilience._chokepoint_topology(G, "adult") == []


def test_find_chokepoints_rejects_unknown_profile_before_touching_the_database():
    G = dumbbell_graph()
    with pytest.raises(ValueError):
        resilience.find_chokepoints(G, engine=object(), profile="bogus")


def test_topology_empty_graph_with_no_usable_edges_returns_no_chokepoints():
    """_chokepoint_topology's own early return (S.number_of_nodes() == 0)
    for a graph where nothing at all survives the edge_allowed filter --
    distinct from test_no_chokepoints_in_a_fully_connected_ring, which has
    real usable edges but zero bridges among them. Here self-loops (the
    `u == v` skip) are the only edges present, so pair_keys stays empty and
    S never gets a single node added."""
    G = nx.MultiDiGraph()
    G.add_node(1, x=0.0, y=0.0)
    G.add_node(2, x=0.0, y=0.0)
    G.add_edge(1, 1, key=0, highway="residential")   # self-loop, skipped by u == v
    assert resilience._chokepoint_topology(G, "adult") == []


def test_chokepoint_topology_isolation_sizes_are_local_to_each_component():
    """Two entirely disconnected islands -- a path 0-1-2-3 and a separate
    path 10-11 -- must each score isolation against their OWN node count,
    not the whole graph's. A bridge can only isolate nodes that were
    reachable through it in the first place, never nodes sitting in an
    already-unreachable island elsewhere in the graph.

    This is the exact hand-traced example used to confirm the bridge-tree
    rewrite's ``local_total`` fix (see resilience.py's docstring and
    docs/CHANGELOG.md's eighteenth-pass entry): the ORIGINAL O(bridges x
    graph) algorithm gets this wrong -- for the 2-3 edge in the 4-node
    island, it reports isolated=3 (comparing against all 6 nodes across
    both islands: min(3, 6-3) = 3) instead of the correct isolated=1
    (comparing only within the 4-node island: min(3, 4-3) = 1) -- a latent
    bug in the old algorithm that was never caught because nothing in this
    project's real or synthetic data was ever a genuinely disconnected,
    multi-island graph until this was checked by hand."""
    G = nx.MultiDiGraph()
    for n in [0, 1, 2, 3, 10, 11]:
        G.add_node(n, x=0.0, y=0.0)
    for u, v in [(0, 1), (1, 2), (2, 3), (10, 11)]:
        two_way_edge(G, u, v)
    topo = resilience._chokepoint_topology(G, "adult")
    got = {tuple(sorted((u, v))): isolated for u, v, _key, isolated in topo}
    assert got == {(0, 1): 1, (1, 2): 2, (2, 3): 1, (10, 11): 1}


def test_chokepoint_topology_runs_in_near_linear_time_on_many_dead_ends():
    """Regression guard for the performance bug found against the real
    55,577-segment Kings Point graph (docs/CHANGELOG.md's eighteenth pass):
    the original algorithm looped over every bridge and, for each one, did
    a full graph copy plus a fresh BFS -- invisible on this suite's small
    hand-built graphs (at most one or two bridges) but O(bridges x
    (|V|+|E|)) overall, which took several minutes on the real graph's
    2,027 bridges. A normal suburban street network is exactly this shape:
    one connected backbone with a large number of single-access dead-end
    spurs, each spur itself a bridge.

    Builds a backbone ring of ``n_hub`` nodes with ``n_spurs`` one-edge
    dead ends hanging off it (2,000 spurs -- smaller than the real graph's
    2,027 bridges but the same shape, kept modest so this test itself
    stays fast) and asserts both correctness (every spur is a bridge that
    isolates exactly its own one node) and that it completes well within
    a linear-time budget. The old algorithm's ~0.15-0.2 s per bridge would
    put 2,000 bridges at several minutes; the O(|V|+|E|) rewrite should
    take a small fraction of a second."""
    n_hub = 50
    n_spurs = 2000
    G = nx.MultiDiGraph()
    for n in range(n_hub):
        G.add_node(n, x=0.0, y=0.0)
    for i in range(n_hub):
        two_way_edge(G, i, (i + 1) % n_hub)   # ring backbone: no bridges by itself
    spur_ids = []
    for s in range(n_spurs):
        spur = 1_000_000 + s
        spur_ids.append(spur)
        G.add_node(spur, x=0.0, y=0.0)
        two_way_edge(G, s % n_hub, spur)   # dead-end spur off the ring: always a bridge

    start = time.monotonic()
    topo = resilience._chokepoint_topology(G, "adult")
    elapsed = time.monotonic() - start

    assert len(topo) == n_spurs   # only the spurs are bridges; the ring itself has none
    isolated_by_spur = {}
    for u, v, _key, isolated in topo:
        spur = u if u in set(spur_ids) else v
        isolated_by_spur[spur] = isolated
    assert set(isolated_by_spur) == set(spur_ids)
    assert all(isolated == 1 for isolated in isolated_by_spur.values())
    # generous budget (the old algorithm would blow through this by orders
    # of magnitude): real hardware runs this in well under a second.
    assert elapsed < 5.0, (
        f"_chokepoint_topology took {elapsed:.2f}s for {n_spurs} bridges -- "
        "this should be O(|V|+|E|), not O(bridges x graph size); see "
        "docs/CHANGELOG.md's eighteenth pass"
    )
