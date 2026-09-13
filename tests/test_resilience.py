"""Network-wide chokepoint (single point of failure) analysis on small
hand-built graphs -- pure topology, no database involved (find_chokepoints()
and chokepoints_geojson() additionally hit the hazard table for per-edge
flood stats; that half is covered against real loaded data in
tests/test_integration.py, alongside the dev_seed grid's own real vehicle
chokepoint -- see test_integration.py's docstring for that case)."""
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
