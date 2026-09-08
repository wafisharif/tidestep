"""Regression test for a real bug found by running the demo path end to
end: ``scripts/dev_seed.py`` used to write its synthetic street graph to
``data/dev_seed_streets.graphml``, a path ``tidestep/api.py`` never reads —
``router()`` always loads the hardcoded ``streets.GRAPH_PATH``
(``data/streets.graphml``). A fresh clone had no graph there at all
(``/api/route`` would 500); a machine with real fetched data already
present would silently route against real-world coordinates while the
segments/hazard rows in PostGIS were the synthetic scenario, miles away —
wrong, and not obviously wrong (`/api/route` still returns 200s).

No network or database needed — ``write_demo_graph`` only touches the
filesystem, so this runs everywhere ``pytest -q tests`` does.
"""
from __future__ import annotations

import sys
from pathlib import Path

import networkx as nx
import osmnx as ox
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dev_seed  # noqa: E402


def _tiny_graph() -> nx.MultiDiGraph:
    G = nx.MultiDiGraph(crs="epsg:4326")
    G.add_node(1, x=-73.70, y=40.90)
    G.add_node(2, x=-73.69, y=40.90)
    G.add_edge(1, 2, key=0, length=100.0, highway="residential", name="test")
    G.add_edge(2, 1, key=0, length=100.0, highway="residential", name="test")
    return G


def test_write_demo_graph_lands_at_the_path_api_actually_loads(tmp_path):
    graph_path = tmp_path / "streets.graphml"
    out = dev_seed.write_demo_graph(_tiny_graph(), graph_path)
    assert out == graph_path
    assert graph_path.exists()
    # must be loadable through osmnx's own loader, the same call api.py
    # makes (ox.save_graphml/ox.load_graphml is a matched pair; plain
    # networkx.write_graphml is not — this is exactly the earlier bug)
    loaded = ox.load_graphml(graph_path)
    assert set(loaded.nodes) == {1, 2}


def test_write_demo_graph_backs_up_an_existing_real_graph_instead_of_deleting_it(tmp_path):
    graph_path = tmp_path / "streets.graphml"
    graph_path.write_text("<!-- pretend this is real fetched data -->")

    dev_seed.write_demo_graph(_tiny_graph(), graph_path)

    backup = graph_path.with_suffix(".graphml.real-backup")
    assert backup.exists()
    assert backup.read_text() == "<!-- pretend this is real fetched data -->"
    assert graph_path.exists()   # now the synthetic graph, not gone


def test_write_demo_graph_never_overwrites_an_existing_backup(tmp_path):
    graph_path = tmp_path / "streets.graphml"
    backup = graph_path.with_suffix(".graphml.real-backup")
    backup.write_text("original real backup, from an earlier demo run")
    graph_path.write_text("a second 'real' graph that should NOT clobber the backup")

    dev_seed.write_demo_graph(_tiny_graph(), graph_path)

    # the first backup survives untouched — re-running the demo repeatedly
    # can never destroy the one real copy
    assert backup.read_text() == "original real backup, from an earlier demo run"
