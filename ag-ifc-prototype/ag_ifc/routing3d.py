"""Orthogonal 3D clash routing on a voxel grid (AEC-implementable polylines)."""

from __future__ import annotations

import heapq
from dataclasses import dataclass

import numpy as np

from ag_ifc.ifc_geometry import Aabb


@dataclass
class Route3D:
    waypoints: list[np.ndarray]
    grid_step_m: float
    clearance_m: float
    reached_goal: bool

    @property
    def net_translation(self) -> np.ndarray:
        if len(self.waypoints) < 2:
            return np.zeros(3)
        return self.waypoints[-1] - self.waypoints[0]

    @property
    def segment_vectors(self) -> list[np.ndarray]:
        segs: list[np.ndarray] = []
        for i in range(len(self.waypoints) - 1):
            segs.append(self.waypoints[i + 1] - self.waypoints[i])
        return segs


def _snap(point: np.ndarray, origin: np.ndarray, step: float) -> tuple[int, int, int]:
    rel = (point - origin) / step
    return (int(round(rel[0])), int(round(rel[1])), int(round(rel[2])))


def _unsnap(cell: tuple[int, int, int], origin: np.ndarray, step: float) -> np.ndarray:
    return origin + np.array(cell, dtype=float) * step


def _cell_blocked(cell: tuple[int, int, int], origin: np.ndarray, step: float, obstacles: list[Aabb]) -> bool:
    point = _unsnap(cell, origin, step)
    for obs in obstacles:
        if obs.contains_point(point):
            return True
    return False


def _manhattan_neighbors(cell: tuple[int, int, int]) -> list[tuple[int, int, int]]:
    i, j, k = cell
    return [
        (i + 1, j, k),
        (i - 1, j, k),
        (i, j + 1, k),
        (i, j - 1, k),
        (i, j, k + 1),
        (i, j, k - 1),
    ]


def _heuristic(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return float(abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2]))


def route_orthogonal(
    start: np.ndarray,
    goal: np.ndarray,
    obstacles: list[Aabb],
    *,
    clearance_m: float = 0.05,
    grid_step_m: float = 0.1,
    max_cells: int = 12000,
) -> Route3D:
    step = max(grid_step_m, 0.05)
    origin = np.minimum(start, goal) - step * 4
    start_cell = _snap(start, origin, step)
    goal_cell = _snap(goal, origin, step)

    open_set: list[tuple[float, tuple[int, int, int]]] = []
    heapq.heappush(open_set, (0.0, start_cell))
    came_from: dict[tuple[int, int, int], tuple[int, int, int] | None] = {start_cell: None}
    g_score: dict[tuple[int, int, int], float] = {start_cell: 0.0}
    visited = 0

    while open_set and visited < max_cells:
        _, current = heapq.heappop(open_set)
        visited += 1
        if current == goal_cell:
            break
        for neighbor in _manhattan_neighbors(current):
            if _cell_blocked(neighbor, origin, step, obstacles):
                continue
            tentative = g_score[current] + 1.0
            if tentative < g_score.get(neighbor, float("inf")):
                came_from[neighbor] = current
                g_score[neighbor] = tentative
                f = tentative + _heuristic(neighbor, goal_cell)
                heapq.heappush(open_set, (f, neighbor))

    if goal_cell not in came_from:
        delta = goal - start
        if np.linalg.norm(delta) < 1e-9:
            delta = np.array([0.0, 0.0, clearance_m])
        direction = delta / max(np.linalg.norm(delta), 1e-9)
        escape = start + direction * max(clearance_m, step)
        return Route3D(
            waypoints=[start.copy(), escape],
            grid_step_m=step,
            clearance_m=clearance_m,
            reached_goal=False,
        )

    path_cells: list[tuple[int, int, int]] = []
    cell: tuple[int, int, int] | None = goal_cell
    while cell is not None:
        path_cells.append(cell)
        cell = came_from.get(cell)
    path_cells.reverse()

    waypoints = [_unsnap(c, origin, step) for c in path_cells]
    collapsed = [waypoints[0]]
    for pt in waypoints[1:]:
        if len(collapsed) == 1:
            collapsed.append(pt)
            continue
        prev_dir = collapsed[-1] - collapsed[-2]
        cur_dir = pt - collapsed[-1]
        if np.linalg.norm(prev_dir) > 1e-9 and np.linalg.norm(cur_dir) > 1e-9:
            prev_u = prev_dir / np.linalg.norm(prev_dir)
            cur_u = cur_dir / np.linalg.norm(cur_dir)
            if np.linalg.norm(np.cross(prev_u, cur_u)) < 1e-6:
                collapsed[-1] = pt
                continue
        collapsed.append(pt)

    return Route3D(
        waypoints=collapsed,
        grid_step_m=step,
        clearance_m=clearance_m,
        reached_goal=True,
    )


def goal_point_from_clash(
    clash: dict,
    movable_geom_center: np.ndarray,
    *,
    clearance_m: float,
    step_m: float,
) -> np.ndarray:
    p1 = np.array(clash.get("p1") or movable_geom_center, dtype=float)
    p2 = np.array(clash.get("p2") or p1, dtype=float)
    axis = p2 - p1
    norm = np.linalg.norm(axis)
    if norm < 1e-9:
        axis = np.array([0.0, 0.0, 1.0])
        norm = 1.0
    direction = axis / norm
    distance = max(clearance_m, step_m) + clearance_m
    return movable_geom_center + direction * distance
