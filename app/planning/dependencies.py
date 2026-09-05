"""Dependency graph validation (brief section 14).

The model proposes dependencies; this module decides which ones survive. A
cycle, a self-dependency, or an edge pointing at a task that does not exist
would make the project unschedulable, so each is removed and reported rather
than allowed to reach the scheduler.

Removals are returned, never swallowed: a dropped dependency changes the plan
and the PM has to see that it happened.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import networkx as nx


@dataclass
class DependencyIssue:
    kind: str  # self_reference | unknown_task | cycle
    detail: str
    removed_edges: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class DependencyGraph:
    graph: nx.DiGraph
    issues: list[DependencyIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.issues

    def dependencies_of(self, task_id: str) -> list[str]:
        """Task ids that must finish before `task_id` can start."""
        return list(self.graph.predecessors(task_id))

    def topological_order(self) -> list[str]:
        return list(nx.topological_sort(self.graph))


def build_dependency_graph(tasks: dict[str, list[str]]) -> DependencyGraph:
    """Build a validated DAG from {task_id: [ids it depends on]}.

    Edges run from dependency to dependent, matching the direction work flows.
    """
    graph = nx.DiGraph()
    graph.add_nodes_from(tasks)
    issues: list[DependencyIssue] = []

    for task_id, dependencies in tasks.items():
        for dependency in dependencies:
            if dependency == task_id:
                issues.append(
                    DependencyIssue(
                        "self_reference",
                        f"{task_id} depends on itself",
                        [(dependency, task_id)],
                    )
                )
                continue
            if dependency not in tasks:
                issues.append(
                    DependencyIssue(
                        "unknown_task",
                        f"{task_id} depends on unknown task {dependency}",
                        [(dependency, task_id)],
                    )
                )
                continue
            graph.add_edge(dependency, task_id)

    issues += _break_cycles(graph)
    return DependencyGraph(graph=graph, issues=issues)


def _break_cycles(graph: nx.DiGraph) -> list[DependencyIssue]:
    """Remove one edge per cycle so the graph can be scheduled.

    The edge closing the cycle is dropped rather than an arbitrary one: it is
    the dependency that made the loop, and reporting it names the actual
    contradiction for the PM to resolve.
    """
    issues: list[DependencyIssue] = []
    while True:
        try:
            cycle = nx.find_cycle(graph, orientation="original")
        except nx.NetworkXNoCycle:
            return issues
        closing_edge = (cycle[-1][0], cycle[-1][1])
        graph.remove_edge(*closing_edge)
        path = " -> ".join(edge[0] for edge in cycle) + f" -> {cycle[-1][1]}"
        issues.append(
            DependencyIssue(
                "cycle",
                f"circular dependency {path}; removed {closing_edge[0]} -> {closing_edge[1]}",
                [closing_edge],
            )
        )
