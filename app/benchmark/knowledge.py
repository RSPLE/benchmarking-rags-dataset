from __future__ import annotations

import json
from pathlib import Path

from app.benchmark.storage import fingerprint


def read_snapshot(path):
    snapshot = json.loads(Path(path).read_text())
    graph = snapshot.get("graph", {})
    if snapshot.get("schema") != 1 or fingerprint(graph) != snapshot.get("sha256"):
        raise ValueError("Invalid knowledge snapshot identity")
    nodes, edges = graph.get("nodes"), graph.get("edges")
    if not isinstance(nodes, list) or not nodes or not isinstance(edges, list):
        raise ValueError("Knowledge snapshot requires nodes and edges")
    names = [node.get("nome") for node in nodes if isinstance(node, dict)]
    if (
        len(names) != len(nodes)
        or any(not isinstance(n, str) or not n for n in names)
        or len(set(names)) != len(names)
    ):
        raise ValueError("Invalid or duplicate knowledge nodes")
    for edge in edges:
        if (
            not isinstance(edge, dict)
            or edge.get("source") not in names
            or edge.get("target") not in names
            or not isinstance(edge.get("relation"), str)
        ):
            raise ValueError("Invalid knowledge edge")
    return graph
