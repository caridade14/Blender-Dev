# SPDX-License-Identifier: GPL-3.0-or-later
"""UV algorithms using Blender mesh data, without context-sensitive operators.

All mutations require object mode and an existing active UV map. Pins are
protected by skipping the entire affected island (or geometry component when
stacking repeats). Selection means mesh-face selection, not UV-editor selection.
These functions neither create seams nor alter mesh geometry.
"""

from collections import defaultdict, deque
import math


_UV_EPS = 1.0e-6
_AREA_EPS = 1.0e-12


def _mesh(obj):
    if obj is None or getattr(obj, "type", None) != "MESH":
        raise TypeError("A mesh object is required")
    if getattr(obj, "mode", "OBJECT") != "OBJECT":
        raise ValueError("UV data operations require Object Mode")
    return obj.data


def _loops(mesh, faces):
    return [li for fi in faces for li in mesh.polygons[fi].loop_indices]


def _pinned(layer, loops):
    return any(layer.pin[li].value for li in loops)


def _different(a, b, tolerance=1.0e-10):
    return abs(a[0] - b[0]) > tolerance or abs(a[1] - b[1]) > tolerance


def uv_islands(obj, selected_only=False):
    """Return deterministic lists of face indices connected across UV edges.

    Adjacency requires a shared mesh edge and matching UVs at *both* of its
    corresponding vertices, within 1e-6 UV units. Coincident unrelated faces do
    not connect. With selected_only, only selected mesh faces participate.
    """
    mesh = _mesh(obj)
    layer = mesh.uv_layers.active
    if layer is None:
        return []
    faces = [p.index for p in mesh.polygons if not selected_only or p.select]
    adjacency = {fi: set() for fi in faces}
    by_edge = defaultdict(list)
    for fi in faces:
        polygon = mesh.polygons[fi]
        loops = list(polygon.loop_indices)
        for i, li in enumerate(loops):
            nxt = loops[(i + 1) % len(loops)]
            a = mesh.loops[li].vertex_index
            b = mesh.loops[nxt].vertex_index
            endpoints = {a: tuple(layer.data[li].uv), b: tuple(layer.data[nxt].uv)}
            by_edge[mesh.loops[li].edge_index].append((fi, endpoints))
    tol2 = _UV_EPS * _UV_EPS
    for candidates in by_edge.values():
        for i, (fa, ea) in enumerate(candidates):
            for fb, eb in candidates[i + 1:]:
                if fa == fb or ea.keys() != eb.keys():
                    continue
                if all(sum((ea[v][k] - eb[v][k]) ** 2 for k in (0, 1)) <= tol2 for v in ea):
                    adjacency[fa].add(fb)
                    adjacency[fb].add(fa)
    remaining = set(faces)
    result = []
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        queue = deque([start])
        island = []
        while queue:
            fi = queue.popleft()
            island.append(fi)
            for other in sorted(adjacency[fi]):
                if other in remaining:
                    remaining.remove(other)
                    queue.append(other)
        result.append(sorted(island))
    return result


def orient_islands(obj, axis='X', selected_only=False):
    """Align each unpinned island's principal UV direction to X or Y.

    A longest-edge direction resolves nearly isotropic PCA shapes. Rotation is
    around the mean UV position, preserves scale, and returns changed islands.
    """
    mesh = _mesh(obj)
    axis = axis.upper()
    if axis not in {'X', 'Y'}:
        raise ValueError("UV orientation axis must be X or Y")
    layer = mesh.uv_layers.active
    if layer is None:
        return 0
    changed = 0
    for faces in uv_islands(obj, selected_only=selected_only):
        loops = _loops(mesh, faces)
        if not loops or _pinned(layer, loops):
            continue
        coordinates = [tuple(layer.data[li].uv) for li in loops]
        cx = sum(p[0] for p in coordinates) / len(coordinates)
        cy = sum(p[1] for p in coordinates) / len(coordinates)
        xx = sum((p[0] - cx) ** 2 for p in coordinates)
        yy = sum((p[1] - cy) ** 2 for p in coordinates)
        xy = sum((p[0] - cx) * (p[1] - cy) for p in coordinates)
        if xx + yy <= _AREA_EPS:
            continue
        anisotropy = math.hypot(xx - yy, 2.0 * xy)
        if anisotropy <= 1.0e-8 * (xx + yy):
            longest = (0.0, 1.0, 0.0)
            for fi in faces:
                face_loops = list(mesh.polygons[fi].loop_indices)
                for i, li in enumerate(face_loops):
                    a = layer.data[li].uv
                    b = layer.data[face_loops[(i + 1) % len(face_loops)]].uv
                    dx, dy = b.x - a.x, b.y - a.y
                    if dx * dx + dy * dy > longest[0]:
                        longest = (dx * dx + dy * dy, dx, dy)
            direction = math.atan2(longest[2], longest[1])
        else:
            direction = 0.5 * math.atan2(2.0 * xy, xx - yy)
        angle = ((0.0 if axis == 'X' else math.pi / 2.0) - direction + math.pi / 2.0) % math.pi - math.pi / 2.0
        if abs(angle) <= 1.0e-8:
            continue
        cosine, sine = math.cos(angle), math.sin(angle)
        for li, (x, y) in zip(loops, coordinates):
            dx, dy = x - cx, y - cy
            layer.data[li].uv = (cx + cosine * dx - sine * dy, cy + sine * dx + cosine * dy)
        changed += 1
    if changed:
        mesh.update_gpu_tag()
    return changed


def normalize_density(obj, selected_only=False):
    """Give every unpinned island one UV-area unit per world-area unit.

    Scale is uniform about each island's mean loop UV, retaining its shape and
    position. World area includes the object transform. Selection means mesh
    faces; pinned islands are left untouched. Any eligible island with zero,
    near-zero (1e-12), or non-finite world/UV area raises ValueError before any
    UV coordinate changes. Returns the number of islands whose scale changed.
    A subsequent common atlas scale preserves these equal relative densities.
    """
    mesh = _mesh(obj)
    layer = mesh.uv_layers.active
    if layer is None:
        return 0
    eligible = []
    owner = {}
    for faces in uv_islands(obj, selected_only=selected_only):
        loops = _loops(mesh, faces)
        if _pinned(layer, loops):
            continue
        index = len(eligible)
        eligible.append(dict(loops=loops, world_area=0.0, uv_area=0.0))
        for fi in faces:
            owner[fi] = index
    if not eligible:
        return 0
    mesh.calc_loop_triangles()
    for triangle in mesh.loop_triangles:
        index = owner.get(triangle.polygon_index)
        if index is None:
            continue
        item = eligible[index]
        points = [obj.matrix_world @ mesh.vertices[vi].co for vi in triangle.vertices]
        item['world_area'] += (points[1] - points[0]).cross(points[2] - points[0]).length / 2.0
        uv = [layer.data[li].uv.copy() for li in triangle.loops]
        edge1, edge2 = uv[1] - uv[0], uv[2] - uv[0]
        item['uv_area'] += abs(edge1.x * edge2.y - edge1.y * edge2.x) / 2.0
    assignments = []
    changed = 0
    for item in eligible:
        world_area, uv_area = item['world_area'], item['uv_area']
        if (not math.isfinite(world_area) or not math.isfinite(uv_area) or
                world_area <= _AREA_EPS or uv_area <= _AREA_EPS):
            raise ValueError("Density normalization requires nonzero, finite world and UV area on every unpinned island")
        scale = math.sqrt(world_area / uv_area)
        if abs(scale - 1.0) <= 1.0e-8:
            continue
        coordinates = [tuple(layer.data[li].uv) for li in item['loops']]
        cx = sum(p[0] for p in coordinates) / len(coordinates)
        cy = sum(p[1] for p in coordinates) / len(coordinates)
        for li, (x, y) in zip(item['loops'], coordinates):
            coordinate = (cx + (x - cx) * scale, cy + (y - cy) * scale)
            if not all(math.isfinite(value) and abs(value) <= 3.4028234e38 for value in coordinate):
                raise ValueError("Normalized UV coordinates exceed Blender's finite coordinate range")
            assignments.append((li, coordinate))
        changed += 1
    for li, coordinate in assignments:
        layer.data[li].uv = coordinate
    if changed:
        mesh.update_gpu_tag()
    return changed


def uv_quality(obj):
    """Measure triangulated UV quality against world-space mesh geometry.

    Flips use signed UV triangle area; zero areas use a 1e-12 UV-area threshold.
    Stretch is the worst singular-value expansion/compression after normalizing
    total UV area to total world surface area. Thus uniform global scaling has
    stretch 1, while shear and inconsistent density increase it. Mean stretch
    is world-area weighted. Degenerate mappings yield infinity. Flips are
    reported separately and do not increase the unsigned stretch measure.
    """
    mesh = _mesh(obj)
    layer = mesh.uv_layers.active
    result = dict(islands=0, flipped_triangles=0, zero_area_triangles=0,
                  max_stretch=0.0, mean_stretch=0.0)
    if layer is None:
        return result
    result['islands'] = len(uv_islands(obj))
    mesh.calc_loop_triangles()
    triangles = []
    world_total = uv_total = 0.0
    for triangle in mesh.loop_triangles:
        points = [obj.matrix_world @ mesh.vertices[vi].co for vi in triangle.vertices]
        uv = [layer.data[li].uv.copy() for li in triangle.loops]
        edge1, edge2 = points[1] - points[0], points[2] - points[0]
        doubled_world = edge1.cross(edge2).length
        world_area = doubled_world / 2.0
        du, dv = uv[1] - uv[0], uv[2] - uv[0]
        signed_uv_area = (du.x * dv.y - du.y * dv.x) / 2.0
        if signed_uv_area < -_AREA_EPS:
            result['flipped_triangles'] += 1
        if abs(signed_uv_area) <= _AREA_EPS:
            result['zero_area_triangles'] += 1
        world_total += world_area
        uv_total += abs(signed_uv_area)
        triangles.append((edge1, edge2, doubled_world, du, dv, world_area, abs(signed_uv_area)))
    if not triangles:
        return result
    normalization = math.sqrt(world_total / uv_total) if world_total > _AREA_EPS and uv_total > _AREA_EPS else 0.0
    stretches = []
    for edge1, edge2, double_area, du, dv, area, uv_area in triangles:
        if area <= _AREA_EPS or uv_area <= _AREA_EPS or not normalization:
            stretch = math.inf
        else:
            length = edge1.length
            bx, by = edge1.dot(edge2) / length, double_area / length
            col1 = du / length
            col2 = (dv - col1 * bx) / by
            aa = col1.length_squared
            bb = col2.length_squared
            ab = col1.dot(col2)
            largest = math.sqrt(max(0.0, (aa + bb + math.hypot(aa - bb, 2.0 * ab)) / 2.0))
            determinant = abs(col1.x * col2.y - col1.y * col2.x)
            smallest = determinant / largest if largest else 0.0
            stretch = max(largest * normalization, 1.0 / (smallest * normalization)) if smallest > 0 else math.inf
        stretches.append((stretch, area))
    result['max_stretch'] = max(s for s, _ in stretches)
    result['mean_stretch'] = (sum(s * area for s, area in stretches if area > _AREA_EPS) / world_total
                              if world_total > _AREA_EPS else math.inf)
    return result


def _bounds(layer, loops):
    points = [layer.data[li].uv for li in loops]
    return (min(p.x for p in points), min(p.y for p in points),
            max(p.x for p in points), max(p.y for p in points))


def _rect_intersects(a, b, gap):
    return (a[0] < b[2] + gap - 1.0e-10 and a[2] > b[0] - gap + 1.0e-10 and
            a[1] < b[3] + gap - 1.0e-10 and a[3] > b[1] - gap + 1.0e-10)


def _shelf_layout(items, obstacles, tiles, margin, scale):
    """Deterministic height-sorted shelves; pinned bounds obstruct each row."""
    occupied = [list(obstacles.get(i, ())) for i in range(tiles)]
    states = [[margin, margin, 0.0] for _ in range(tiles)]
    output = {}
    ordered = sorted(items, key=lambda item: (-item['height'], -item['width'], item['index']))
    for item in ordered:
        width = max(item['width'] * scale, 1.0e-9)
        height = max(item['height'] * scale, 1.0e-9)
        if width > 1.0 - 2.0 * margin + 1.0e-10 or height > 1.0 - 2.0 * margin + 1.0e-10:
            return None
        placed = False
        for tile in range(tiles):
            x, y, row_height = states[tile]
            # Every iteration passes an obstacle or advances a shelf.
            for _ in range(4 * (len(occupied[tile]) + len(items) + 2)):
                if y + height > 1.0 - margin + 1.0e-10:
                    break
                if x + width > 1.0 - margin + 1.0e-10:
                    if row_height > 0.0:
                        y += row_height + margin
                    else:
                        tops = [r[3] + margin for r in occupied[tile]
                                if r[3] + margin > y + 1.0e-10]
                        if not tops:
                            break
                        y = min(tops)
                    x, row_height = margin, 0.0
                    continue
                rectangle = (x, y, x + width, y + height)
                hits = [r for r in occupied[tile] if _rect_intersects(rectangle, r, margin)]
                if hits:
                    x = max(r[2] + margin for r in hits)
                    continue
                occupied[tile].append(rectangle)
                states[tile] = [x + width + margin, y, max(row_height, height)]
                output[item['index']] = (tile, x, y)
                placed = True
                break
            if placed:
                break
        if not placed:
            return None
    return output


def distribute_udim(obj, tiles=1, margin=0.01, preserve_density=False):
    """Pack whole unpinned islands into UDIM 1001 onward, using ten columns.

    Deterministic shelf packing conservatively prevents bounding-box overlap.
    Margin is measured in tile UV units, both at borders and between islands.
    Pinned islands stay fixed and their bounds obstruct the requested tiles.
    preserve_density=True forbids rescaling and raises ValueError when this
    packing cannot fit. False allows one shared uniform scale (up or down),
    preserving relative density between islands. No UVs change on failure.
    Returns the number of packed unpinned islands. Packing is conservative,
    not an optimal nesting algorithm, and never rotates an island.
    """
    mesh = _mesh(obj)
    if isinstance(tiles, bool) or int(tiles) != tiles or not 1 <= tiles <= 1000:
        raise ValueError("UDIM tile count must be an integer from 1 to 1000")
    tiles = int(tiles)
    if not math.isfinite(margin) or not 0.0 <= margin < 0.5:
        raise ValueError("Margin must be finite and between 0 and 0.5")
    layer = mesh.uv_layers.active
    if layer is None:
        return 0
    items, pinned = [], []
    for index, faces in enumerate(uv_islands(obj)):
        loops = _loops(mesh, faces)
        bounds = _bounds(layer, loops)
        if not all(math.isfinite(value) for value in bounds):
            raise ValueError("UV coordinates must be finite before packing")
        if _pinned(layer, loops):
            pinned.append(bounds)
        else:
            items.append(dict(index=index, loops=loops, bounds=bounds,
                              width=bounds[2] - bounds[0], height=bounds[3] - bounds[1]))
    if not items:
        return 0
    obstacles = defaultdict(list)
    for tile in range(tiles):
        u, v = tile % 10, tile // 10
        for bounds in pinned:
            local = (bounds[0] - u, bounds[1] - v, bounds[2] - u, bounds[3] - v)
            if local[2] >= 0 and local[0] <= 1 and local[3] >= 0 and local[1] <= 1:
                obstacles[tile].append(local)
    scale = 1.0
    layout = _shelf_layout(items, obstacles, tiles, margin, scale)
    if preserve_density:
        if layout is None:
            raise ValueError("The islands do not fit these tiles at their current density; add tiles or allow scaling")
    else:
        max_extent = max(max(item['width'], item['height']) for item in items)
        upper = (1.0 - 2.0 * margin) / max(max_extent, 1.0e-9)
        # Preserve a known valid solution: shelf packing is heuristic, so only
        # tested successes are retained rather than assuming strict monotonicity.
        lower = 0.0
        best_layout, best_scale = layout, 1.0 if layout is not None else 0.0
        for _ in range(48):
            candidate = (lower + upper) / 2.0
            candidate_layout = _shelf_layout(items, obstacles, tiles, margin, candidate)
            if candidate_layout is None:
                upper = candidate
            else:
                lower = candidate
                if candidate > best_scale:
                    best_layout, best_scale = candidate_layout, candidate
        if best_layout is None:
            raise ValueError("Pinned islands or tile margins leave no room for this packing")
        layout, scale = best_layout, best_scale
    assignments = []
    for item in items:
        tile, x, y = layout[item['index']]
        u, v = tile % 10, tile // 10
        xmin, ymin = item['bounds'][:2]
        for li in item['loops']:
            current = layer.data[li].uv
            assignments.append((li, (u + x + (current.x - xmin) * scale,
                                     v + y + (current.y - ymin) * scale)))
    for li, coordinate in assignments:
        layer.data[li].uv = coordinate
    mesh.update_gpu_tag()
    return len(items)


def _components(mesh):
    adjacency = defaultdict(set)
    for edge in mesh.edges:
        a, b = edge.vertices
        adjacency[a].add(b)
        adjacency[b].add(a)
    owner, components = {}, []
    for vertex in mesh.vertices:
        if vertex.index in owner:
            continue
        index = len(components)
        queue, vertices = deque([vertex.index]), []
        owner[vertex.index] = index
        while queue:
            vi = queue.popleft()
            vertices.append(vi)
            for other in sorted(adjacency[vi]):
                if other not in owner:
                    owner[other] = index
                    queue.append(other)
        components.append(dict(vertices=vertices, faces=[], edges=[]))
    for face in mesh.polygons:
        components[owner[face.vertices[0]]]['faces'].append(face.index)
    for edge in mesh.edges:
        components[owner[edge.vertices[0]]]['edges'].append(tuple(edge.vertices))
    return [c for c in components if c['faces']]


def _cycle(vertices, unoriented=False):
    sequence = tuple(vertices)
    options = [sequence[i:] + sequence[:i] for i in range(len(sequence))]
    if unoriented:
        reverse = tuple(reversed(sequence))
        options.extend(reverse[i:] + reverse[:i] for i in range(len(reverse)))
    return min(options)


def _normalized_component(mesh, component, tolerance):
    coords = {vi: tuple(mesh.vertices[vi].co) for vi in component['vertices']}
    minimum = tuple(min(point[k] for point in coords.values()) for k in range(3))
    extent = max(max(point[k] for point in coords.values()) - minimum[k] for k in range(3))
    if extent <= 1.0e-12:
        return None
    normalized = {vi: tuple((point[k] - minimum[k]) / extent for k in range(3))
                  for vi, point in coords.items()}
    ordered = sorted(normalized, key=lambda vi: normalized[vi])
    # Ambiguous coincident vertices are deliberately not guessed.
    bins = defaultdict(list)
    for vi in ordered:
        point = normalized[vi]
        key = tuple(math.floor(value / tolerance) for value in point)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    if any(sum((point[k] - normalized[other][k]) ** 2 for k in range(3)) <= tolerance ** 2
                           for other in bins.get((key[0] + dx, key[1] + dy, key[2] + dz), ())):
                        return None
        bins[key].append(vi)
    return ordered, normalized


def stack_repeats(obj, tolerance=1.0e-5):
    """Stack exact disconnected geometry duplicates; return changed components.

    Matching permits translation and positive *uniform* scale in object-local
    coordinates, with normalized coordinate tolerance. It requires identical
    vertex/edge/face topology and winding. UV loops are mapped by face and
    vertex, preserving every source seam. Connected cube faces are never
    mistaken for repeated objects. Rotation, reflection, ambiguous coincident
    vertices, and any target component with UV pins are conservatively skipped.
    """
    mesh = _mesh(obj)
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Tolerance must be finite and positive")
    layer = mesh.uv_layers.active
    if layer is None:
        return 0
    groups = defaultdict(list)
    changed = 0
    for component in _components(mesh):
        normalized = _normalized_component(mesh, component, tolerance)
        if normalized is None:
            continue
        ordered, coordinates = normalized
        local_index = {vi: i for i, vi in enumerate(ordered)}
        edges = {tuple(sorted((local_index[a], local_index[b]))) for a, b in component['edges']}
        faces = {}
        ambiguous = False
        for fi in component['faces']:
            key = _cycle([local_index[vi] for vi in mesh.polygons[fi].vertices])
            if key in faces:
                ambiguous = True
                break
            faces[key] = fi
        if ambiguous:
            continue
        signature = (len(ordered), len(edges), tuple(sorted(len(key) for key in faces)))
        matched = None
        for source in groups[signature]:
            if edges != source['edges'] or faces.keys() != source['faces'].keys():
                continue
            if all(sum((coordinates[vi][k] - source['coordinates'][source['ordered'][i]][k]) ** 2
                       for k in range(3)) <= tolerance ** 2 for i, vi in enumerate(ordered)):
                matched = source
                break
        if matched is None:
            groups[signature].append(dict(ordered=ordered, coordinates=coordinates,
                                          edges=edges, faces=faces))
            continue
        if _pinned(layer, _loops(mesh, component['faces'])):
            continue
        assignments = []
        for key, target_fi in faces.items():
            source_fi = matched['faces'][key]
            source_loop_by_vertex = {mesh.loops[li].vertex_index: li for li in mesh.polygons[source_fi].loop_indices}
            for li in mesh.polygons[target_fi].loop_indices:
                target_vi = mesh.loops[li].vertex_index
                source_vi = matched['ordered'][local_index[target_vi]]
                source_li = source_loop_by_vertex[source_vi]
                assignments.append((li, tuple(layer.data[source_li].uv)))
        if any(_different(layer.data[li].uv, uv) for li, uv in assignments):
            for li, uv in assignments:
                layer.data[li].uv = uv
            changed += 1
    if changed:
        mesh.update_gpu_tag()
    return changed


def mirror_uv(obj, axis='X', offset=0.0, tolerance=1.0e-4):
    """Stack mirrored negative-side islands onto positive-side UVs.

    The symmetry plane is object-local axis = offset (X, Y or Z). Geometry is
    unchanged. Complete mirrored face topology and unique vertex matches are
    required; crossing-plane islands, unmatched islands, and pinned targets
    are skipped. Source loop UVs transfer by mirrored vertex correspondence,
    retaining source seams. Returns the number of changed target islands.
    """
    mesh = _mesh(obj)
    axis = axis.upper()
    if axis not in {'X', 'Y', 'Z'}:
        raise ValueError("Geometry symmetry axis must be X, Y or Z")
    if not math.isfinite(offset) or not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("Offset must be finite and tolerance must be positive")
    layer = mesh.uv_layers.active
    if layer is None:
        return 0
    dimension = 'XYZ'.index(axis)
    bins = defaultdict(list)
    for vertex in mesh.vertices:
        key = tuple(math.floor(value / tolerance) for value in vertex.co)
        bins[key].append(vertex.index)
    mirror_vertices = {}
    for vertex in mesh.vertices:
        point = list(vertex.co)
        point[dimension] = 2.0 * offset - point[dimension]
        key = tuple(math.floor(value / tolerance) for value in point)
        candidates = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for vi in bins.get((key[0] + dx, key[1] + dy, key[2] + dz), ()):
                        if sum((mesh.vertices[vi].co[k] - point[k]) ** 2 for k in range(3)) <= tolerance ** 2:
                            candidates.append(vi)
        if len(candidates) == 1:
            mirror_vertices[vertex.index] = candidates[0]
    faces_by_vertices = defaultdict(list)
    for face in mesh.polygons:
        faces_by_vertices[_cycle(face.vertices, unoriented=True)].append(face.index)
    mirrored_faces = {}
    for face in mesh.polygons:
        distances = [mesh.vertices[vi].co[dimension] - offset for vi in face.vertices]
        if max(distances) > tolerance or min(distances) >= -tolerance:
            continue
        if not all(vi in mirror_vertices for vi in face.vertices):
            continue
        key = _cycle([mirror_vertices[vi] for vi in face.vertices], unoriented=True)
        candidates = faces_by_vertices.get(key, ())
        if len(candidates) != 1 or candidates[0] == face.index:
            continue
        source_face = mesh.polygons[candidates[0]]
        source_distances = [mesh.vertices[vi].co[dimension] - offset for vi in source_face.vertices]
        if min(source_distances) < -tolerance or max(source_distances) <= tolerance:
            continue
        mirrored_faces[face.index] = source_face.index
    changed = 0
    for island in uv_islands(obj):
        target_loops = _loops(mesh, island)
        if _pinned(layer, target_loops) or not all(fi in mirrored_faces for fi in island):
            continue
        assignments = []
        for target_fi in island:
            source_face = mesh.polygons[mirrored_faces[target_fi]]
            source_loops = {mesh.loops[li].vertex_index: li for li in source_face.loop_indices}
            for li in mesh.polygons[target_fi].loop_indices:
                source_vi = mirror_vertices[mesh.loops[li].vertex_index]
                assignments.append((li, tuple(layer.data[source_loops[source_vi]].uv)))
        if any(_different(layer.data[li].uv, uv) for li, uv in assignments):
            for li, uv in assignments:
                layer.data[li].uv = uv
            changed += 1
    if changed:
        mesh.update_gpu_tag()
    return changed
