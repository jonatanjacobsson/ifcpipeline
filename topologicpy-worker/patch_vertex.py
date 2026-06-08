"""Apply upstream-style None guards to installed topologicpy Vertex.py (build-time)."""
from __future__ import annotations

from pathlib import Path


def main() -> None:
    import topologicpy

    path = Path(topologicpy.__file__).resolve().parent / "Vertex.py"
    text = path.read_text(encoding="utf-8")
    if "if v_proj is None or not Vertex.IsInternal" in text:
        print(f"already patched: {path}")
        return

    text = text.replace(
        "            if not Vertex.IsInternal(v_proj, face):",
        "            if v_proj is None or not Vertex.IsInternal(v_proj, face):",
        1,
    )
    text = text.replace(
        "            dic = Face.PlaneEquation(face)\n            a = dic[\"a\"]",
        "            dic = Face.PlaneEquation(face)\n"
        "            if dic is None:\n"
        "                vertices = Topology.Vertices(face)\n"
        "                distances = [distance_to_vertex(vertex, v) for v in vertices]\n"
        "                edges = Topology.Edges(face)\n"
        "                distances += [distance_to_edge(vertex, e) for e in edges]\n"
        "                if includeCentroid:\n"
        "                    distances.append(distance_to_vertex(vertex, Topology.Centroid(face)))\n"
        "                return min(distances)\n"
        "            a = dic[\"a\"]",
        1,
    )
    text = text.replace(
        "        eq = Face.PlaneEquation(face, mantissa= mantissa)\n        if direction == None or direction == []:",
        "        eq = Face.PlaneEquation(face, mantissa= mantissa)\n"
        "        if eq is None:\n"
        "            return None\n"
        "        if direction == None or direction == []:",
        1,
    )
    path.write_text(text, encoding="utf-8")
    print(f"patched: {path}")


if __name__ == "__main__":
    main()
