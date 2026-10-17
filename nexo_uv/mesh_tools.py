"""Geometry inspection, part detection, and seam planning for Nexo UV.

The algorithms operate on a temporary BMesh and leave topology, transforms,
materials, and existing UV maps untouched.  No native extension is required.
"""

from collections import deque
import heapq
import math

import bmesh


PART_ATTRIBUTE = "nexo_part"
_MODES = {"ANGLE", "CONNECTED", "MATERIAL", "SEAMS"}
_STYLES = {"HARD_SURFACE", "ORGANIC", "CLOTH"}


def _mesh(obj):
    if obj is None or getattr(obj, "type", None) != "MESH":
        raise TypeError("Choose a mesh object.")
    if getattr(obj, "mode", "OBJECT") != "OBJECT":
        raise ValueError("Mesh tools require Object Mode.")
    return obj.data


def _load_bmesh(mesh):
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()
        bm.normal_update()
        return bm
    except Exception:
        bm.free()
        raise


def _angle_cosine(angle):
    angle = float(angle)
    if not math.isfinite(angle):
        raise ValueError("The seam angle must be finite.")
    return math.cos(max(0.0, min(math.pi, angle)))


class _DisjointSet:
    def __init__(self, count):
        self.parent = list(range(count))
        self.rank = [0] * count

    def find(self, item):
        parent = self.parent
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(self, first, second):
        first, second = self.find(first), self.find(second)
        if first == second:
            return False
        if self.rank[first] < self.rank[second]:
            first, second = second, first
        self.parent[second] = first
        if self.rank[first] == self.rank[second]:
            self.rank[first] += 1
        return True


def analyze_mesh(obj):
    """Return local-space diagnostics without changing any mesh data.

    ``nonmanifold_edges`` includes boundary edges, wire edges (no faces), and
    edges shared by more than two faces. ``wire_edges`` reports the wire subset
    separately. Triangle count is the polygon tessellation count, not a change
    to the original polygons.
    """
    mesh = _mesh(obj)
    bm = _load_bmesh(mesh)
    try:
        if bm.verts:
            extent = max(
                max(vertex.co[axis] for vertex in bm.verts)
                - min(vertex.co[axis] for vertex in bm.verts)
                for axis in range(3)
            )
        else:
            extent = 0.0
        area_epsilon = max(extent * extent * 1.0e-14, 1.0e-30)
        return {
            "vertices": len(bm.verts),
            "edges": len(bm.edges),
            "faces": len(bm.faces),
            "degenerate_faces": sum(
                face.calc_area() <= area_epsilon for face in bm.faces
            ),
            "nonmanifold_edges": sum(len(edge.link_faces) != 2 for edge in bm.edges),
            "boundary_edges": sum(len(edge.link_faces) == 1 for edge in bm.edges),
            "wire_edges": sum(not edge.link_faces for edge in bm.edges),
            "loose_vertices": sum(not vertex.link_edges for vertex in bm.verts),
            "triangles": sum(max(0, len(face.verts) - 2) for face in bm.faces),
        }
    finally:
        bm.free()


def segment_mesh(obj, mode="ANGLE", angle=math.radians(55), respect_materials=True):
    """Return deterministic, contiguous face labels and the number of parts.

    ANGLE crosses smooth manifold edges, CONNECTED crosses shared edges,
    MATERIAL crosses same-material edges, and SEAMS crosses unmarked edges.
    Material boundaries can also constrain the other modes. Nonmanifold
    junctions are split in ANGLE mode; CONNECTED supports such junctions.
    Labels are ordered by the first polygon in each component.
    """
    mesh = _mesh(obj)
    mode = str(mode).upper()
    if mode not in _MODES:
        raise ValueError("Unknown part detection mode: " + mode)
    cosine = _angle_cosine(angle)
    bm = _load_bmesh(mesh)
    try:
        groups = _DisjointSet(len(bm.faces))
        for edge in bm.edges:
            faces = edge.link_faces
            if len(faces) < 2:
                continue
            if mode == "SEAMS" and edge.seam:
                continue
            if mode == "ANGLE":
                if len(faces) != 2:
                    continue
                if faces[0].normal.dot(faces[1].normal) < cosine - 1.0e-7:
                    continue
            if respect_materials or mode == "MATERIAL":
                first_by_material = {}
                for face in faces:
                    first = first_by_material.setdefault(face.material_index, face.index)
                    groups.union(first, face.index)
            else:
                first = faces[0].index
                for face in faces[1:]:
                    groups.union(first, face.index)
        identifiers = {}
        labels = []
        for face in bm.faces:
            root = groups.find(face.index)
            if root not in identifiers:
                identifiers[root] = len(identifiers)
            labels.append(identifiers[root])
        return labels, len(identifiers)
    finally:
        bm.free()


def persist_part_labels(obj, labels):
    """Store part IDs in a FACE/INT attribute without editing geometry."""
    mesh = _mesh(obj)
    labels = [int(label) for label in labels]
    if len(labels) != len(mesh.polygons):
        raise ValueError("Part labels must contain one entry per polygon.")
    if any(label < 0 for label in labels):
        raise ValueError("Part labels cannot be negative.")
    attribute = mesh.attributes.get(PART_ATTRIBUTE)
    if attribute is not None and (
        attribute.domain != "FACE" or attribute.data_type != "INT"
    ):
        raise ValueError("The nexo_part attribute must use the FACE domain and INT type.")
    if attribute is None:
        attribute = mesh.attributes.new(PART_ATTRIBUTE, "INT", "FACE")
    attribute.data.foreach_set("value", labels)
    mesh.update()


def read_part_labels(obj):
    """Return saved face IDs, or None when a compatible attribute is absent."""
    mesh = _mesh(obj)
    attribute = mesh.attributes.get(PART_ATTRIBUTE)
    if attribute is None or attribute.domain != "FACE" or attribute.data_type != "INT":
        return None
    labels = [0] * len(attribute.data)
    attribute.data.foreach_get("value", labels)
    return labels


def _components(bm):
    """Face-connected components, including nonmanifold junctions."""
    groups = _DisjointSet(len(bm.faces))
    for edge in bm.edges:
        if len(edge.link_faces) > 1:
            first = edge.link_faces[0].index
            for face in edge.link_faces[1:]:
                groups.union(first, face.index)
    components = {}
    for face in bm.faces:
        components.setdefault(groups.find(face.index), []).append(face)
    return list(components.values())


def _cut_geometry(faces, symmetry_axis):
    """Choose deterministic poles and edge weights for a rear/central cut."""
    vertices = {vertex.index: vertex for face in faces for vertex in face.verts}
    edges = {edge.index: edge for face in faces for edge in face.edges}
    minimum = [min(vertex.co[axis] for vertex in vertices.values()) for axis in range(3)]
    maximum = [max(vertex.co[axis] for vertex in vertices.values()) for axis in range(3)]
    extent = [maximum[axis] - minimum[axis] for axis in range(3)]
    center = [(minimum[axis] + maximum[axis]) * 0.5 for axis in range(3)]
    # A Z-first tie break gives upright characters a predictable rear seam.
    axial = max((2, 1, 0), key=lambda axis: extent[axis])
    bilateral = {"X": 0, "Y": 1, "Z": 2}[symmetry_axis]
    if bilateral == axial:
        bilateral = next(axis for axis in (0, 1, 2) if axis != axial)
    rear = next(axis for axis in (1, 0, 2) if axis not in (axial, bilateral))
    span = max(max(extent), 1.0e-15)

    def normalized(vertex, axis):
        return (vertex.co[axis] - center[axis]) / max(extent[axis], span * 1.0e-8)

    def pole_key(vertex, upper=False):
        axial_coordinate = -vertex.co[axial] if upper else vertex.co[axial]
        return (axial_coordinate, abs(normalized(vertex, bilateral)),
                normalized(vertex, rear), vertex.index)

    first = min(vertices.values(), key=pole_key).index
    last = min(vertices.values(), key=lambda vertex: pole_key(vertex, True)).index
    weights = {}
    for index, edge in edges.items():
        v1, v2 = edge.verts
        front = (normalized(v1, rear) + normalized(v2, rear)) * 0.5 + 0.5
        side = (normalized(v1, bilateral) + normalized(v2, bilateral)) * 0.5
        length = max(edge.calc_length(), span * 1.0e-9)
        weights[index] = length * (1.0 + 8.0 * front * front + 4.0 * side * side)
    return vertices, edges, weights, first, last


def _shortest_tree(vertices, edges, weights, root):
    adjacency = {index: [] for index in vertices}
    for index, edge in edges.items():
        first, second = (vertex.index for vertex in edge.verts)
        adjacency[first].append((second, index))
        adjacency[second].append((first, index))
    distances = {root: 0.0}
    parents = {}
    heap = [(0.0, root)]
    while heap:
        distance, current = heapq.heappop(heap)
        if distance != distances[current]:
            continue
        for neighbor, edge_index in adjacency[current]:
            candidate = distance + weights[edge_index]
            if candidate < distances.get(neighbor, math.inf):
                distances[neighbor] = candidate
                parents[neighbor] = (current, edge_index)
                heapq.heappush(heap, (candidate, neighbor))
    return parents


def _surface_cut(faces, symmetry_axis, closed):
    """Build a path for open cloth, or a pruned tree/cotree cut for a shell.

    A longitudinal slit opens a genus-zero shell. For shells with handles, the
    primal-tree/dual-tree construction additionally retains the cycles needed
    to open the handles, instead of relying on a sphere-only meridian heuristic.
    """
    vertices, edges, weights, first, last = _cut_geometry(faces, symmetry_axis)
    parents = _shortest_tree(vertices, edges, weights, first)
    if not closed:
        path = set()
        current = last
        while current in parents:
            current, edge_index = parents[current]
            path.add(edge_index)
        return path

    primal = {edge_index for _, edge_index in parents.values()}
    face_indices = {face.index: index for index, face in enumerate(faces)}
    dual_groups = _DisjointSet(len(faces))
    dual = set()
    # Prefer retaining low-cost rear edges as the residual handle cuts.
    candidates = sorted(
        (index for index in edges if index not in primal),
        key=lambda index: (-weights[index], index),
    )
    for index in candidates:
        linked = edges[index].link_faces
        if len(linked) == 2 and dual_groups.union(
            face_indices[linked[0].index], face_indices[linked[1].index]
        ):
            dual.add(index)
    cut = set(edges) - dual
    incidence = {index: set() for index in vertices}
    for index in cut:
        for vertex in edges[index].verts:
            incidence[vertex.index].add(index)
    protected = {first, last}
    leaves = deque(index for index in vertices if len(incidence[index]) == 1
                   and index not in protected)
    while leaves:
        vertex_index = leaves.popleft()
        if vertex_index in protected or len(incidence[vertex_index]) != 1:
            continue
        edge_index = next(iter(incidence[vertex_index]))
        cut.remove(edge_index)
        for vertex in edges[edge_index].verts:
            incidence[vertex.index].discard(edge_index)
            if len(incidence[vertex.index]) == 1 and vertex.index not in protected:
                leaves.append(vertex.index)
    return cut


def plan_seams(obj, style="HARD_SURFACE", angle=math.radians(55),
               respect_existing=True, labels=None, symmetry_axis="X", hide_seams=False):
    """Plan seams in local coordinates and return the resulting seam count.

    HARD_SURFACE uses dihedral angles and part boundaries. ORGANIC favors a
    centered rear cut and opens handles on closed manifold shells. CLOTH adds
    boundaries and a principal-axis cut. Existing seams are preserved by
    default. ``hide_seams`` enables rear-biased automatic cuts for closed hard
    surfaces as well; it never hides geometry in the viewport.
    """
    mesh = _mesh(obj)
    style = str(style).upper()
    symmetry_axis = str(symmetry_axis).upper()
    if style not in _STYLES:
        raise ValueError("Unknown seam style: " + style)
    if symmetry_axis not in {"X", "Y", "Z"}:
        raise ValueError("Symmetry axis must be X, Y, or Z.")
    cosine = _angle_cosine(angle)
    if labels is not None:
        labels = list(labels)
        if len(labels) != len(mesh.polygons):
            raise ValueError("Part labels must contain one entry per polygon.")
    bm = _load_bmesh(mesh)
    try:
        planned = {edge.index for edge in bm.edges if respect_existing and edge.seam}
        for edge in bm.edges:
            linked = edge.link_faces
            if len(linked) == 1:
                planned.add(edge.index)
            elif len(linked) > 2:
                # Nonmanifold junctions cannot form a continuous UV chart.
                planned.add(edge.index)
            if len(linked) == 2:
                if labels is not None and labels[linked[0].index] != labels[linked[1].index]:
                    planned.add(edge.index)
                if style == "HARD_SURFACE" and (
                    linked[0].normal.dot(linked[1].normal) < cosine - 1.0e-7
                ):
                    planned.add(edge.index)

        for faces in _components(bm):
            edges = {edge for face in faces for edge in face.edges}
            closed = all(len(edge.link_faces) == 2 for edge in edges)
            has_interior_cut = any(
                edge.index in planned and len(edge.link_faces) == 2 for edge in edges
            )
            if style == "CLOTH":
                planned.update(_surface_cut(faces, symmetry_axis, closed))
            elif closed and (
                style == "ORGANIC" or hide_seams or not has_interior_cut
            ):
                planned.update(_surface_cut(faces, symmetry_axis, True))

        # Write only seam flags; a BMesh round-trip could disturb unrelated layers.
        for edge in mesh.edges:
            edge.use_seam = edge.index in planned
        mesh.update()
        return len(planned)
    finally:
        bm.free()


def select_part(obj, part_id):
    """Select a saved part's faces, edges, and vertices; return face count."""
    mesh = _mesh(obj)
    labels = read_part_labels(obj)
    if labels is None:
        raise ValueError("Detect parts before selecting a part.")
    target = int(part_id)
    vertices, edges = set(), set()
    selected = 0
    for polygon, label in zip(mesh.polygons, labels):
        polygon.select = label == target
        if polygon.select:
            selected += 1
            vertices.update(polygon.vertices)
            edges.update(mesh.loops[index].edge_index for index in polygon.loop_indices)
    for edge in mesh.edges:
        edge.select = edge.index in edges
    for vertex in mesh.vertices:
        vertex.select = vertex.index in vertices
    mesh.update()
    return selected
