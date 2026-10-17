"""Exercise Nexo UV inside a real Blender process.

Run from the repository root::

    blender --background --factory-startup --python-exit-code 1 --python tests/blender_smoke.py

The suite creates disposable Blender scenes and does not need external assets,
native add-on binaries, a display server, or third-party Python packages.
"""

from __future__ import annotations

import math
from pathlib import Path
import sys
import tempfile
import unittest

import bpy


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nexo_uv  # noqa: E402


EPSILON = 1.0e-5


def activate(*objects):
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0] if objects else None


def cube(name="Cube", offset=0.0):
    bpy.ops.mesh.primitive_cube_add(location=(offset, 0, 0))
    obj = bpy.context.object
    obj.name = name
    return obj


def coordinates(obj, layer_name="NexoUV"):
    layer = obj.data.uv_layers.get(layer_name)
    if layer is None:
        return None
    return tuple(tuple(float(value) for value in entry.uv) for entry in layer.data)


def assign_uv(obj, layer_name="NexoUV"):
    layer = obj.data.uv_layers.get(layer_name) or obj.data.uv_layers.new(name=layer_name)
    for index, entry in enumerate(layer.data):
        # Deliberately distinctive values make accidental writes detectable.
        entry.uv = (0.03125 * index, 0.0625 * (index % 5))
    layer.pin.foreach_set("value", [False] * len(obj.data.loops))
    return coordinates(obj, layer_name)


def uv_flags(layer):
    """Capture coordinates and flags without using add-on snapshot helpers."""
    count = len(layer.uv)
    values, pins, selected, edges = [0.0] * (count * 2), [False] * count, [False] * count, [False] * count
    layer.uv.foreach_get("vector", values)
    layer.pin.foreach_get("value", pins)
    layer.vertex_selection.foreach_get("value", selected)
    layer.edge_selection.foreach_get("value", edges)
    return tuple(values), tuple(pins), tuple(selected), tuple(edges)


def uv_state(obj):
    return (obj.data.uv_layers.active_index,
            tuple((layer.name, layer.active_render, layer.active_clone, uv_flags(layer))
                  for layer in obj.data.uv_layers))


def uv_surface_area(obj):
    values = coordinates(obj)
    total = 0.0
    for polygon in obj.data.polygons:
        corners = [values[index] for index in polygon.loop_indices]
        total += abs(sum(a[0] * b[1] - b[0] * a[1]
                         for a, b in zip(corners, corners[1:] + corners[:1]))) / 2
    return total


def island_bounds(obj):
    """Find UV-connected faces without depending on add-on internals."""
    mesh = obj.data
    layer = mesh.uv_layers.get("NexoUV")
    face_uvs = []
    edge_faces = {}
    for poly in mesh.polygons:
        uvs = {mesh.loops[i].vertex_index: tuple(layer.data[i].uv) for i in poly.loop_indices}
        face_uvs.append(uvs)
        for edge in poly.edge_keys:
            edge_faces.setdefault(tuple(sorted(edge)), []).append(poly.index)
    neighbors = [set() for _ in mesh.polygons]
    for edge, faces in edge_faces.items():
        for left in faces:
            for right in faces:
                if left != right and all(
                    all(abs(a - b) < EPSILON for a, b in zip(face_uvs[left][v], face_uvs[right][v]))
                    for v in edge
                ):
                    neighbors[left].add(right)
    remaining = set(range(len(mesh.polygons)))
    bounds = []
    while remaining:
        todo = [remaining.pop()]
        component = []
        while todo:
            face = todo.pop()
            component.append(face)
            connected = neighbors[face] & remaining
            remaining.difference_update(connected)
            todo.extend(connected)
        values = [uv for face in component for uv in face_uvs[face].values()]
        bounds.append((min(p[0] for p in values), min(p[1] for p in values),
                       max(p[0] for p in values), max(p[1] for p in values)))
    return bounds


class NexoUVSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        nexo_uv.register()

    @classmethod
    def tearDownClass(cls):
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        nexo_uv.unregister()

    def setUp(self):
        if bpy.context.object and bpy.context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete(use_global=False)
        settings = bpy.context.scene.nexo_uv
        settings.style = "HARD_SURFACE"
        settings.uv_name = "NexoUV"
        settings.backup_uv = True
        settings.selected_only = False
        settings.pack_after = True
        settings.udim_tiles = 1
        settings.respect_existing = True
        settings.segment_mode = "ANGLE"
        settings.seam_angle = math.radians(66)
        settings.preserve_density = False
        settings.average_density = True
        settings.orient_after = False

    def assertFinished(self, operator, **kwargs):
        self.assertIn("FINISHED", operator(**kwargs))

    def assertUsableUV(self, obj):
        values = coordinates(obj)
        self.assertIsNotNone(values, "The requested NexoUV map was not created")
        self.assertTrue(all(math.isfinite(c) for pair in values for c in pair))
        # At least one face must have two-dimensional area.
        mesh = obj.data
        areas = []
        for face in mesh.polygons:
            corners = [values[index] for index in face.loop_indices]
            areas.append(abs(sum(a[0] * b[1] - b[0] * a[1]
                                 for a, b in zip(corners, corners[1:] + corners[:1]))) / 2)
        self.assertGreater(max(areas), EPSILON)

    def test_01_registration_can_be_repeated(self):
        for _ in range(2):
            nexo_uv.unregister()
            self.assertFalse(hasattr(bpy.types.Scene, "nexo_uv"))
            nexo_uv.register()
            self.assertTrue(hasattr(bpy.types.Scene, "nexo_uv"))

    def test_02_cube_unwrap_creates_finite_nonzero_uvs(self):
        obj = cube()
        self.assertFinished(bpy.ops.nexo_uv.unwrap)
        self.assertUsableUV(obj)
        self.assertEqual(bpy.context.object, obj)
        self.assertEqual(obj.mode, "OBJECT")
        self.assertEqual(set(bpy.context.selected_objects), {obj})

    def test_03_backup_restores_original_uv_values(self):
        obj = cube()
        before = assign_uv(obj)
        self.assertFinished(bpy.ops.nexo_uv.unwrap)
        self.assertNotEqual(coordinates(obj), before)
        self.assertFinished(bpy.ops.nexo_uv.restore_uv)
        for actual, expected in zip(coordinates(obj), before):
            self.assertAlmostEqual(actual[0], expected[0], places=6)
            self.assertAlmostEqual(actual[1], expected[1], places=6)

    def test_04_organic_and_cloth_workflows(self):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8)
        sphere = bpy.context.object
        bpy.context.scene.nexo_uv.style = "ORGANIC"
        self.assertFinished(bpy.ops.nexo_uv.segment)
        self.assertFinished(bpy.ops.nexo_uv.unwrap)
        self.assertUsableUV(sphere)
        bpy.ops.mesh.primitive_cylinder_add(vertices=16)
        cylinder = bpy.context.object
        activate(cylinder)
        bpy.context.scene.nexo_uv.style = "CLOTH"
        self.assertFinished(bpy.ops.nexo_uv.unwrap)
        self.assertUsableUV(cylinder)

    def test_05_multiple_objects_and_unselected_object(self):
        first = cube("First")
        second = cube("Second", 3)
        sentinel = cube("Untouched", 6)
        before = assign_uv(sentinel)
        activate(first, second)
        self.assertFinished(bpy.ops.nexo_uv.unwrap)
        self.assertUsableUV(first)
        self.assertUsableUV(second)
        self.assertEqual(coordinates(sentinel), before)
        self.assertEqual(set(bpy.context.selected_objects), {first, second})
        self.assertEqual(bpy.context.view_layer.objects.active, first)

    def test_06_selected_face_scope_and_edit_context(self):
        obj = cube()
        before = assign_uv(obj)
        for face in obj.data.polygons:
            face.select = face.index == 0
        bpy.context.tool_settings.mesh_select_mode = (False, False, True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.context.scene.nexo_uv.selected_only = True
        self.assertFinished(bpy.ops.nexo_uv.unwrap)
        self.assertEqual(obj.mode, "EDIT")
        self.assertEqual(bpy.context.view_layer.objects.active, obj)
        bpy.ops.object.mode_set(mode="OBJECT")
        self.assertEqual([face.index for face in obj.data.polygons if face.select], [0])
        after = coordinates(obj)
        for face in obj.data.polygons:
            if face.index != 0:
                for loop in face.loop_indices:
                    self.assertEqual(after[loop], before[loop], "Unselected faces must keep their UVs")

    def test_07_shared_mesh_rejects_without_corrupting_data(self):
        first = cube("Original")
        before = assign_uv(first)
        linked = bpy.data.objects.new("Linked", first.data)
        bpy.context.collection.objects.link(linked)
        activate(first, linked)
        try:
            result = bpy.ops.nexo_uv.unwrap()
        except RuntimeError as error:
            self.assertTrue(str(error).strip())
        else:
            self.assertIn("CANCELLED", result)
        self.assertEqual(coordinates(first), before)
        self.assertEqual(first.data, linked.data)
        self.assertEqual(first.mode, "OBJECT")

    def test_08_analyze_handles_open_and_loose_geometry(self):
        mesh = bpy.data.meshes.new("NonManifold")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0), (2, 0, 0), (3, 0, 0)],
                         [(3, 4)], [(0, 1, 2)])
        obj = bpy.data.objects.new("OpenAndLoose", mesh)
        bpy.context.collection.objects.link(obj)
        activate(obj)
        before = tuple(tuple(v.co) for v in mesh.vertices)
        self.assertFinished(bpy.ops.nexo_uv.analyze)
        self.assertEqual(tuple(tuple(v.co) for v in mesh.vertices), before)

    def test_09_pack_stays_inside_tiles_and_separates_cube_islands(self):
        obj = cube()
        self.assertFinished(bpy.ops.nexo_uv.segment)
        self.assertFinished(bpy.ops.nexo_uv.unwrap)
        settings = bpy.context.scene.nexo_uv
        settings.udim_tiles = 2
        self.assertFinished(bpy.ops.nexo_uv.pack)
        bounds = island_bounds(obj)
        self.assertGreaterEqual(len(bounds), 2)
        for left, bottom, right, top in bounds:
            self.assertGreaterEqual(left, -EPSILON)
            self.assertGreaterEqual(bottom, -EPSILON)
            self.assertLessEqual(right, 2 + EPSILON)
            self.assertLessEqual(top, 1 + EPSILON)
        for index, a in enumerate(bounds):
            for b in bounds[index + 1:]:
                overlap_x = min(a[2], b[2]) - max(a[0], b[0])
                overlap_y = min(a[3], b[3]) - max(a[1], b[1])
                self.assertFalse(overlap_x > EPSILON and overlap_y > EPSILON,
                                 "Packed rectangular cube islands must not overlap")

    def test_10_relax_preserves_pinned_uv(self):
        obj = cube()
        self.assertFinished(bpy.ops.nexo_uv.unwrap)
        layer = obj.data.uv_layers["NexoUV"]
        layer.pin[0].value = True
        layer.data[0].uv = (0.123, 0.456)
        expected = tuple(layer.data[0].uv)
        self.assertFinished(bpy.ops.nexo_uv.relax)
        # Mode transitions and backup creation invalidate Blender RNA handles.
        layer = obj.data.uv_layers["NexoUV"]
        self.assertEqual(tuple(layer.data[0].uv), expected)
        self.assertTrue(layer.pin[0].value)
        self.assertUsableUV(obj)

    def test_11_other_tools_are_available_in_background(self):
        obj = cube()
        self.assertFinished(bpy.ops.nexo_uv.smart_project)
        self.assertUsableUV(obj)
        self.assertFinished(bpy.ops.nexo_uv.analyze)
        self.assertFinished(bpy.ops.nexo_uv.preview_seams)
        self.assertFinished(bpy.ops.nexo_uv.quality)
        self.assertFinished(bpy.ops.nexo_uv.orient)
        self.assertFinished(bpy.ops.nexo_uv.mirror)
        self.assertFinished(bpy.ops.nexo_uv.match_seams)
        self.assertFinished(bpy.ops.nexo_uv.stack)
        self.assertFinished(bpy.ops.nexo_uv.select_island)
        self.assertFinished(bpy.ops.nexo_uv.new_part)
        self.assertFinished(bpy.ops.nexo_uv.select_part)
        self.assertFinished(bpy.ops.nexo_uv.merge_parts)
        self.assertFinished(bpy.ops.nexo_uv.reset_parts)
        # Join a real adjacent patch, and verify its boundary protects the UVs
        # of every other face rather than merely checking operator availability.
        first_face = obj.data.polygons[0]
        second_face = next(face for face in obj.data.polygons
                           if face.index != first_face.index
                           and len(set(face.vertices) & set(first_face.vertices)) == 2)
        chosen = {first_face.index, second_face.index}
        for face in obj.data.polygons:
            face.select = face.index in chosen
        previous = coordinates(obj)
        previous_count = len(island_bounds(obj))
        self.assertFinished(bpy.ops.nexo_uv.join_islands)
        self.assertEqual(len(island_bounds(obj)), previous_count - 1)
        current = coordinates(obj)
        for face in obj.data.polygons:
            if face.index not in chosen:
                for loop in face.loop_indices:
                    self.assertEqual(current[loop], previous[loop])
        self.assertUsableUV(obj)

    def test_12_part_labels_follow_topology_without_geometry_changes(self):
        from nexo_uv import mesh_tools

        obj = cube()
        before_vertices = tuple(tuple(v.co) for v in obj.data.vertices)
        before_uv = assign_uv(obj)
        labels, count = mesh_tools.segment_mesh(obj, mode="ANGLE", angle=math.radians(60))
        self.assertEqual(count, 6, "Six perpendicular cube faces should form six crease regions")
        self.assertEqual(len(labels), len(obj.data.polygons))
        self.assertEqual(set(labels), set(range(6)))
        mesh_tools.persist_part_labels(obj, labels)
        self.assertEqual(mesh_tools.read_part_labels(obj), labels)
        connected, count = mesh_tools.segment_mesh(obj, mode="CONNECTED")
        self.assertEqual(count, 1)
        self.assertEqual(set(connected), {0})
        self.assertEqual(tuple(tuple(v.co) for v in obj.data.vertices), before_vertices)
        self.assertEqual(coordinates(obj), before_uv)

    def test_13_uv_data_tools_protect_pinned_islands_and_reject_oversize_atomically(self):
        from nexo_uv import uv_tools

        bpy.ops.mesh.primitive_plane_add()
        obj = bpy.context.object
        layer = obj.data.uv_layers.new(name="NexoUV")
        obj.data.uv_layers.active = layer
        for entry, uv in zip(layer.data, [(0.1, 0.1), (0.4, 0.1), (0.4, 0.3), (0.1, 0.3)]):
            entry.uv = uv
        layer.pin.foreach_set("value", [False] * len(obj.data.loops))
        layer.pin[0].value = True
        before = coordinates(obj)
        self.assertEqual(uv_tools.orient_islands(obj, axis="Y"), 0)
        self.assertEqual(coordinates(obj), before)
        uv_tools.distribute_udim(obj, tiles=1, margin=0.01)
        self.assertEqual(coordinates(obj), before)
        uv_tools.mirror_uv(obj, axis="X")
        self.assertEqual(coordinates(obj), before)
        self.assertTrue(layer.pin[0].value)
        layer.pin[0].value = False
        for entry in layer.data:
            entry.uv *= 20
        oversized = coordinates(obj)
        with self.assertRaises(ValueError):
            uv_tools.distribute_udim(obj, tiles=1, margin=0.01, preserve_density=True)
        self.assertEqual(coordinates(obj), oversized,
                         "A failed density-preserving pack must leave existing coordinates untouched")

    def test_14_shared_atlas_respects_world_scale_and_has_no_cross_object_overlap(self):
        first = cube("UnitScale")
        second = cube("DoubleScale", 6)
        second.scale = (2, 2, 2)
        bpy.context.view_layer.update()
        activate(first, second)
        bpy.context.scene.nexo_uv.average_density = True
        self.assertFinished(bpy.ops.nexo_uv.unwrap)
        self.assertUsableUV(first)
        self.assertUsableUV(second)
        self.assertAlmostEqual(uv_surface_area(second) / uv_surface_area(first), 4.0, delta=0.001,
                               msg="Twice the world-space scale needs four times the UV area")
        for a in island_bounds(first):
            for b in island_bounds(second):
                overlap_x = min(a[2], b[2]) - max(a[0], b[0])
                overlap_y = min(a[3], b[3]) - max(a[1], b[1])
                self.assertFalse(overlap_x > EPSILON and overlap_y > EPSILON,
                                 "Different objects must share one non-overlapping atlas")

    def test_15_failed_pack_rolls_back_all_meshes_backups_seams_and_context(self):
        first = cube("RollbackFirst")
        second = cube("RollbackSecond", 4)
        for obj in (first, second):
            assign_uv(obj)
            layer = obj.data.uv_layers["NexoUV"]
            for index, entry in enumerate(layer.uv):
                entry.vector = (20 + index, 20 + index % 5)
            # The solver must preserve this coordinate outside the permitted tile.
            # Packing will fail after seam planning and backup creation have run.
            layer.pin[0].value = True
            layer.vertex_selection.foreach_set("value", [i % 2 == 0 for i in range(len(layer.uv))])
            layer.edge_selection.foreach_set("value", [i % 3 == 0 for i in range(len(layer.uv))])
            obj.data.uv_layers["NexoUV"].active_render = True
            obj.data.uv_layers["NexoUV"].active_clone = True
            for edge in obj.data.edges:
                edge.use_seam = edge.index % 2 == 0
            obj.data.uv_layers.active = obj.data.uv_layers["UVMap"]
        # One object already has a retained backup; the other must not acquire
        # one as a side effect of the failed operation.
        assign_uv(first, "NexoUV__backup")
        first.data.uv_layers.active = first.data.uv_layers["UVMap"]
        activate(first, second)
        bpy.context.scene.nexo_uv.preserve_density = True
        # Normalize edit-mode selection once before taking the baseline.
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.object.mode_set(mode="OBJECT")
        expected = {obj.name: (uv_state(obj), tuple(e.use_seam for e in obj.data.edges),
                               tuple(p.select for p in obj.data.polygons)) for obj in (first, second)}
        bpy.ops.object.mode_set(mode="EDIT")
        self.assertIn("CANCELLED", bpy.ops.nexo_uv.unwrap())
        self.assertEqual(first.mode, "EDIT")
        self.assertEqual(second.mode, "EDIT")
        self.assertEqual(bpy.context.view_layer.objects.active, first)
        self.assertEqual(set(bpy.context.selected_objects), {first, second})
        bpy.ops.object.mode_set(mode="OBJECT")
        for obj in (first, second):
            actual = (uv_state(obj), tuple(e.use_seam for e in obj.data.edges),
                      tuple(p.select for p in obj.data.polygons))
            self.assertEqual(actual, expected[obj.name],
                             "A failed shared unwrap must restore every original map, flag and seam")

    def test_16_backup_flags_render_choice_and_saved_project_survive_reopen(self):
        obj = cube("PersistentUV")
        assign_uv(obj)
        layer = obj.data.uv_layers["NexoUV"]
        count = len(layer.uv)
        layer.pin.foreach_set("value", [i % 3 == 0 for i in range(count)])
        layer.vertex_selection.foreach_set("value", [i % 2 == 0 for i in range(count)])
        layer.edge_selection.foreach_set("value", [i % 5 == 0 for i in range(count)])
        obj.data.uv_layers["NexoUV"].active_render = True
        obj.data.uv_layers["NexoUV"].active_clone = True
        self.assertTrue(obj.data.uv_layers["NexoUV"].active_render)
        original = uv_flags(obj.data.uv_layers["NexoUV"])
        self.assertFinished(bpy.ops.nexo_uv.orient)
        self.assertEqual(uv_flags(obj.data.uv_layers["NexoUV__backup"]), original)
        self.assertTrue(obj.data.uv_layers["NexoUV"].active_render)
        self.assertTrue(obj.data.uv_layers["NexoUV"].active_clone)
        self.assertFinished(bpy.ops.nexo_uv.restore_uv)
        self.assertEqual(uv_flags(obj.data.uv_layers["NexoUV"]), original)
        self.assertTrue(obj.data.uv_layers["NexoUV"].active_render)
        self.assertTrue(obj.data.uv_layers["NexoUV"].active_clone)
        long_name = "NexoUV_" + "x" * 53
        original_long = assign_uv(obj, long_name)
        bpy.context.scene.nexo_uv.uv_name = long_name
        self.assertFinished(bpy.ops.nexo_uv.unwrap)
        self.assertNotEqual(coordinates(obj, long_name), original_long)
        self.assertFinished(bpy.ops.nexo_uv.restore_uv)
        self.assertEqual(coordinates(obj, long_name), original_long,
                         "UV map names near Blender's length limit need a recoverable backup")
        self.assertTrue(all(len(layer.name) <= 63 for layer in obj.data.uv_layers))
        bpy.context.scene.nexo_uv.uv_name = "NexoUV"
        self.assertFinished(bpy.ops.nexo_uv.segment)
        bpy.context.scene.nexo_uv.style = "ORGANIC"
        saved = uv_state(obj)
        saved_parts = tuple(entry.value for entry in obj.data.attributes["nexo_part"].data)
        with tempfile.TemporaryDirectory(prefix="nexo_uv_smoke_") as directory:
            project = str(Path(directory) / "persistent_uv.blend")
            self.assertFinished(bpy.ops.wm.save_as_mainfile, filepath=project)
            self.assertFinished(bpy.ops.wm.open_mainfile, filepath=project)
            reloaded = bpy.data.objects["PersistentUV"]
            self.assertEqual(uv_state(reloaded), saved)
            self.assertEqual(tuple(entry.value for entry in reloaded.data.attributes["nexo_part"].data),
                             saved_parts)
            self.assertEqual(bpy.context.scene.nexo_uv.style, "ORGANIC")
            activate(reloaded)
            self.assertFinished(bpy.ops.nexo_uv.quality)

    def test_17_part_preview_preserves_user_colors_materials_and_uvs(self):
        obj = cube("Preview")
        assign_uv(obj)
        self.assertFinished(bpy.ops.nexo_uv.segment)
        colors = obj.data.color_attributes.new("UserColors", 'BYTE_COLOR', 'CORNER')
        colors.data.foreach_set('color', [0.2, 0.4, 0.6, 1.0] * len(obj.data.loops))
        obj.data.color_attributes.active_color = colors
        before_colors = tuple(tuple(item.color) for item in colors.data)
        before_uv = coordinates(obj)
        before_materials = tuple(obj.data.materials)
        self.assertFinished(bpy.ops.nexo_uv.show_parts)
        self.assertEqual(len(obj.data.color_attributes), 2)
        self.assertFinished(bpy.ops.nexo_uv.show_parts)
        self.assertEqual(len(obj.data.color_attributes), 1)
        self.assertEqual(obj.data.color_attributes.active_color.name, "UserColors")
        self.assertEqual(tuple(tuple(item.color) for item in obj.data.color_attributes["UserColors"].data),
                         before_colors)
        self.assertEqual(tuple(obj.data.materials), before_materials)
        self.assertEqual(coordinates(obj), before_uv)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(NexoUVSmoke)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise RuntimeError("Nexo UV Blender smoke suite failed")
    print("NEXO_UV_SMOKE_OK tests=%d blender=%s" % (result.testsRun, bpy.app.version_string))
