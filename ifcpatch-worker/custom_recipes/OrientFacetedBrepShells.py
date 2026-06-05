"""
OrientFacetedBrepShells — unify outward face winding on IfcFacetedBrep / IfcClosedShell

MagiCAD (and some tessellation paths) can yield **IfcClosedShell** meshes where
individual **IfcFace** loops are wound inconsistently. Viewers such as Solibri
then show **reversed** faces even though the shell is closed.

This recipe walks **manifold** triangle (and simple polygon) faces, builds an
**edge → (face, sign)** map, and propagates **flips** so each internal undirected
edge is traversed in **opposite** directions by its two incident faces — the
standard requirement for a consistently oriented closed mesh.

**Limitations:** Non-manifold edges (3+ faces), bow-tie quads, or bad topology
may not fully resolve; such cases are logged.

**Typical pipeline:** ``TessellateElements`` → ``OrientFacetedBrepShells`` (same
``query`` selector).

**Alternative (no IFC rewrite):** IfcOpenShell’s geometry kernel can reorient
``IfcConnectedFaceSet`` at tessellation time: ``IfcConvert --reorient-shells`` or
``geom.settings().set("reorient-shells", True)`` — see
https://docs.ifcopenshell.org/ifcconvert/usage.html and
``tests/test_reorient_kernel_vs_recipe.py`` for comparison with this recipe.

Recipe Name: OrientFacetedBrepShells
"""

from __future__ import annotations

import logging
from typing import Optional

import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.representation as ur
import ifcopenshell.util.selector

from logging import Logger

# Broad selectors (thousands of products) stress ifcopenshell.geom; batched IfcElement scope is preferred.
INTERNAL_PATCH_MAX_PRODUCTS = 500
_ORIENT_SETTINGS_REFRESH_INTERVAL = 100


def _collect_faceted_shells_from_representation(rep) -> list:
    shells: list = []
    for item in rep.Items or ():
        if item.is_a() == "IfcFacetedBrep":
            sh = item.Outer
            if sh is not None and sh.is_a() == "IfcClosedShell":
                shells.append(sh)
    return shells


def _face_outer_polyloops(face) -> list:
    """Return IfcPolyLoop instances for outer bounds only."""
    loops: list = []
    for b in face.Bounds or ():
        if b.is_a() != "IfcFaceOuterBound":
            continue
        bd = b.Bound
        if bd is not None and bd.is_a() == "IfcPolyLoop":
            loops.append(bd)
    return loops


def _canonical_edge(i: int, j: int) -> tuple[int, int]:
    return (i, j) if i < j else (j, i)


def _edge_sign_ordered(lo: int, hi: int, a: int, b: int) -> int:
    """+1 if directed edge along boundary is lo→hi, -1 if hi→lo for canonical (lo,hi)."""
    if a == lo and b == hi:
        return 1
    if a == hi and b == lo:
        return -1
    raise ValueError("edge vertex mismatch")


def _coord_key(cartesian_point, decimals: int) -> tuple[float, float, float]:
    c = list(cartesian_point.Coordinates)
    while len(c) < 3:
        c.append(0.0)
    return (
        round(float(c[0]), decimals),
        round(float(c[1]), decimals),
        round(float(c[2]), decimals),
    )


def _build_face_edge_data(
    shell,
    coord_decimals: int,
) -> tuple[list, dict[tuple[int, int], list[tuple[int, int]]]]:
    """
    Returns:
      faces: list of IfcFace
      edge_to_faces: canonical_edge -> [(face_idx, sign), ...]

    Vertices are welded by **rounded coordinates** (file length units). Tessellated
    IFC often uses **distinct** ``IfcCartesianPoint`` instances for the same corner;
    entity-id welding alone would leave false boundary edges.
    """
    faces_list = list(shell.CfsFaces or ())
    key_to_idx: dict[tuple[float, float, float], int] = {}
    face_loops_idx: list[list[int]] = []

    def point_index(cartesian_point) -> int:
        k = _coord_key(cartesian_point, coord_decimals)
        if k not in key_to_idx:
            key_to_idx[k] = len(key_to_idx)
        return key_to_idx[k]

    for face in faces_list:
        polys = _face_outer_polyloops(face)
        if not polys:
            face_loops_idx.append([])
            continue
        poly = polys[0]
        cps = list(poly.Polygon or ())
        if len(cps) < 3:
            face_loops_idx.append([])
            continue
        idxs = [point_index(p) for p in cps]
        face_loops_idx.append(idxs)

    edge_to: dict[tuple[int, int], list[tuple[int, int]]] = {}

    for fi, idxs in enumerate(face_loops_idx):
        n = len(idxs)
        if n < 3:
            continue
        for k in range(n):
            a, b = idxs[k], idxs[(k + 1) % n]
            lo, hi = _canonical_edge(a, b)
            sgn = _edge_sign_ordered(lo, hi, a, b)
            edge_to.setdefault((lo, hi), []).append((fi, sgn))

    return faces_list, edge_to


def _propagate_flips(
    num_faces: int,
    edge_to: dict[tuple[int, int], list[tuple[int, int]]],
    logger: Logger,
) -> list[bool]:
    """BFS propagate flips: neighbor XOR (same_sign_on_shared_edge)."""
    adj: list[list[tuple[int, bool]]] = [[] for _ in range(num_faces)]
    for ekey, lst in edge_to.items():
        if len(lst) != 2:
            if len(lst) > 2:
                logger.warning(
                    "OrientFacetedBrepShells: non-manifold edge %s (%d faces), skipping adjacency",
                    ekey,
                    len(lst),
                )
            continue
        (fa, sa), (fb, sb) = lst[0], lst[1]
        same = sa == sb
        adj[fa].append((fb, same))
        adj[fb].append((fa, same))

    flip: list[Optional[bool]] = [None] * num_faces
    for start in range(num_faces):
        if flip[start] is not None:
            continue
        flip[start] = False
        stack = [start]
        while stack:
            fa = stack.pop()
            for fb, same in adj[fa]:
                new_flip = bool(flip[fa]) ^ same
                if flip[fb] is None:
                    flip[fb] = new_flip
                    stack.append(fb)
                elif flip[fb] != new_flip:
                    logger.warning(
                        "OrientFacetedBrepShells: conflicting flip for face %s (had %s, need %s)",
                        fb,
                        flip[fb],
                        new_flip,
                    )
    out: list[bool] = []
    for i in range(num_faces):
        v = flip[i]
        out.append(False if v is None else bool(v))
    return out


def _reverse_polyloop_in_place(loop) -> None:
    poly = list(loop.Polygon or ())
    if len(poly) < 3:
        return
    loop.Polygon = tuple(reversed(poly))


def _invert_entire_shell(shell) -> int:
    """Reverse every face's outer polyloop (global mirror). Returns face count."""
    n = 0
    for face in shell.CfsFaces or ():
        for b in face.Bounds or ():
            if b.is_a() != "IfcFaceOuterBound":
                continue
            bd = b.Bound
            if bd is not None and bd.is_a() == "IfcPolyLoop":
                _reverse_polyloop_in_place(bd)
                n += 1
                break
    return n


def mesh_signed_volume_from_geom(shape_geometry) -> float:
    """Signed volume using ifcopenshell mesh (SI / kernel units)."""
    import numpy as np
    import ifcopenshell.util.shape as shape_util

    v = np.asarray(shape_util.get_vertices(shape_geometry))
    faces = np.asarray(shape_util.get_faces(shape_geometry))
    if v.size == 0 or faces.size == 0:
        return 0.0
    s = 0.0
    for tri in faces:
        i0, i1, i2 = int(tri[0]), int(tri[1]), int(tri[2])
        s += float(np.dot(v[i0], np.cross(v[i1], v[i2])) / 6.0)
    return s


def _orient_shell(
    shell,
    coord_decimals: int,
    logger: Logger,
) -> int:
    faces_list, edge_to = _build_face_edge_data(shell, coord_decimals)
    num_faces = len(faces_list)
    if num_faces == 0:
        return 0

    # Only process edges shared by exactly two faces
    clean_edge_to: dict[tuple[int, int], list[tuple[int, int]]] = {
        k: v for k, v in edge_to.items() if len(v) == 2
    }
    flip_flags = _propagate_flips(num_faces, clean_edge_to, logger)
    flipped = 0
    for fi, need in enumerate(flip_flags):
        if not need:
            continue
        face = faces_list[fi]
        for b in face.Bounds or ():
            if b.is_a() != "IfcFaceOuterBound":
                continue
            bd = b.Bound
            if bd is not None and bd.is_a() == "IfcPolyLoop":
                _reverse_polyloop_in_place(bd)
                flipped += 1
                break
    return flipped


class Patcher:
    def __init__(
        self,
        file: ifcopenshell.file,
        logger: Optional[Logger] = None,
        query: str = "IfcFlowFitting",
        coord_decimals: int = 6,
    ):
        """
        :param query: ifcopenshell selector for products to fix (default ``IfcFlowFitting``).
        :param coord_decimals: decimal places for coordinate welding (file units); default 6.
        """
        self.file = file
        self.logger = logger or logging.getLogger(__name__)
        self.query = (query or "IfcFlowFitting").strip()
        if coord_decimals is None:
            cd = 6
        elif isinstance(coord_decimals, str):
            cd = int(float(coord_decimals.strip()))
        else:
            cd = int(coord_decimals)
        self.coord_decimals = cd

    def patch(self) -> None:
        context = ur.get_context(self.file, "Model", "Body", "MODEL_VIEW")
        if not context:
            self.logger.warning("OrientFacetedBrepShells: no Model/Body/MODEL_VIEW context")
            return

        elements = ifcopenshell.util.selector.filter_elements(self.file, self.query)
        products = [e for e in elements if e.is_a("IfcProduct")]
        total_flipped = 0
        shells_done = 0

        n_products = len(products)
        if n_products > INTERNAL_PATCH_MAX_PRODUCTS:
            self.logger.warning(
                "OrientFacetedBrepShells: selector matched %d product(s); "
                "prefer batched IfcElement scope in MagiadTessellateAndOrient for kernel stability.",
                n_products,
            )

        geom_settings = ifcopenshell.geom.settings()
        geom_settings.set("context-ids", [context.id()])

        for idx, prod in enumerate(products):
            if (
                n_products > INTERNAL_PATCH_MAX_PRODUCTS
                and idx > 0
                and idx % _ORIENT_SETTINGS_REFRESH_INTERVAL == 0
            ):
                geom_settings = ifcopenshell.geom.settings()
                geom_settings.set("context-ids", [context.id()])

            try:
                rep = ur.get_representation(prod, context)
                if not rep or rep.RepresentationType != "Brep":
                    continue
                shells = _collect_faceted_shells_from_representation(rep)
                for shell in shells:
                    n = _orient_shell(shell, self.coord_decimals, self.logger)
                    if n:
                        total_flipped += n
                        shells_done += 1
                        self.logger.debug(
                            "OrientFacetedBrepShells: %s manifold-fix flipped %d face loop(s)",
                            getattr(prod, "GlobalId", "?"),
                            n,
                        )
                # Ensure positive global volume (outward normals) after manifold consistency.
                try:
                    shp = ifcopenshell.geom.create_shape(geom_settings, prod)
                    vol = mesh_signed_volume_from_geom(shp.geometry)
                    if vol < 0:
                        inv = 0
                        for shell in shells:
                            inv += _invert_entire_shell(shell)
                        total_flipped += inv
                        self.logger.debug(
                            "OrientFacetedBrepShells: %s volume sign fix inverted %d face loop(s) (vol was %s)",
                            getattr(prod, "GlobalId", "?"),
                            inv,
                            vol,
                        )
                except Exception as e:
                    self.logger.warning(
                        "OrientFacetedBrepShells: could not check volume for %s: %s",
                        getattr(prod, "GlobalId", "?"),
                        e,
                    )
            except Exception as e:
                self.logger.warning(
                    "OrientFacetedBrepShells: skipped product %s: %s",
                    getattr(prod, "GlobalId", "?"),
                    e,
                )

        self.logger.info(
            "OrientFacetedBrepShells: processed %d product(s), %d shell(s) with manifold edits, %d face loop(s) reversed total",
            len(products),
            shells_done,
            total_flipped,
        )

    def get_output(self) -> ifcopenshell.file:
        return self.file


def validate_shell_manifold_orientation(shell, coord_decimals: int = 6) -> tuple[bool, list[str], int]:
    """
    Check **manifold** edges only: each edge shared by exactly two faces must have
    **opposite** directed signs (consistent local orientation).

    Tessellated IFC often has **boundary** edges (triangle soup gaps); those are
    reported as ``boundary_count`` but do not fail validation.

    Returns ``(ok, errors, boundary_edge_count)``. Use the same ``coord_decimals``
    as ``OrientFacetedBrepShells``.
    """
    _, edge_to = _build_face_edge_data(shell, coord_decimals)
    errors: list[str] = []
    boundary_count = 0
    for ekey, lst in edge_to.items():
        if len(lst) == 2:
            if lst[0][1] == lst[1][1]:
                errors.append(f"edge {ekey}: same winding sign on both faces")
        elif len(lst) == 1:
            boundary_count += 1
        else:
            errors.append(f"edge {ekey}: non-manifold ({len(lst)} faces)")
    return len(errors) == 0, errors, boundary_count


def validate_shell_edge_consistency(shell, coord_decimals: int = 6) -> tuple[bool, list[str]]:
    """
    Backward-compatible wrapper: same as ``validate_shell_manifold_orientation``
    but returns only ``(ok, errors)`` (ignores boundary edges in the boolean).
    """
    ok, errs, _ = validate_shell_manifold_orientation(shell, coord_decimals)
    return ok, errs
