# SPDX-License-Identifier: GPL-3.0-or-later
"""User actions backed by Blender's UV solvers and independent data helpers."""
import math
import time
import bpy
from .context import mesh_session, edit_mesh, ensure_uv, read_uv, write_uv, backup_name
from . import mesh_tools, uv_tools, bake_tools, part_preview


def _checked(result, operation):
    if 'FINISHED' not in result:
        raise ValueError(f"{operation} did not complete; no changes were kept")


def _active_uv(obj, settings, backup=True):
    if obj.data.uv_layers.get(settings.uv_name.strip()) is None:
        raise ValueError(f"{obj.name}: UV map '{settings.uv_name}' not found; unwrap first or choose an existing map")
    return ensure_uv(obj, settings.uv_name, backup and settings.backup_uv)


def _combined_pack(objects, settings, normalize=False):
    """Pack all selected meshes into one atlas, then commit loop coordinates.

    The temporary, unlinked mesh is never retained or added to the scene. Its
    topology has no shared vertices between source objects. This gives one
    common scale and avoids stacking separately packed objects accidentally.
    """
    vertices, faces, source_loops, uv_values, pins = [], [], [], [], []
    for obj in objects:
        mesh = obj.data
        layer = mesh.uv_layers.active
        offset = len(vertices)
        vertices.extend(tuple(obj.matrix_world @ v.co) for v in mesh.vertices)
        for face in mesh.polygons:
            if settings.selected_only and (not face.select or face.hide):
                continue
            faces.append([offset + v for v in face.vertices])
            for index in face.loop_indices:
                source_loops.append((obj, index))
                uv_values.append(tuple(layer.data[index].uv))
                pins.append(layer.pin[index].value)
    if not faces:
        raise ValueError("Select some visible faces to pack")
    temporary = bpy.data.meshes.new(".NexoUV_Pack")
    proxy = None
    try:
        temporary.from_pydata(vertices, [], faces)
        temporary.update()
        layer = temporary.uv_layers.new(name="Atlas", do_init=False)
        layer.uv.foreach_set('vector', [v for uv in uv_values for v in uv])
        layer.pin.foreach_set('value', pins)
        proxy = bpy.data.objects.new(".NexoUV_Pack", temporary)
        if normalize and not settings.preserve_density:
            uv_tools.normalize_density(proxy)
        count = uv_tools.distribute_udim(proxy, tiles=settings.udim_tiles,
                                        margin=settings.margin,
                                        preserve_density=settings.preserve_density)
        for (obj, index), loop in zip(source_loops, layer.data):
            obj.data.uv_layers.active.data[index].uv = loop.uv
        for obj in objects:
            obj.data.update_gpu_tag()
        return count
    finally:
        if proxy:
            bpy.data.objects.remove(proxy)
        bpy.data.meshes.remove(temporary)


def _run(operator, context, action, *, selection=False):
    start = time.perf_counter()
    try:
        with mesh_session(context, keep_selection=selection) as objects:
            result = action(objects, context.scene.nexo_uv)
        message = f"{result} · {time.perf_counter() - start:.2f}s"
        context.scene.nexo_uv.status = message
        operator.report({'INFO'}, message)
        return {'FINISHED'}
    except (ValueError, RuntimeError, OSError) as exc:
        context.scene.nexo_uv.status = str(exc)
        operator.report({'WARNING'}, str(exc))
        return {'CANCELLED'}


class _MeshAction:
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'MESH'


class NEXOUV_OT_analyze(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.analyze"
    bl_label = "Analyze Mesh"
    bl_description = "Report topology without repairing or changing the mesh"

    def execute(self, context):
        def action(objects, settings):
            reports = []
            for obj in objects:
                data = mesh_tools.analyze_mesh(obj)
                report = (f"{obj.name}: {data['faces']} faces, {data['degenerate_faces']} degenerate, "
                          f"{data['nonmanifold_edges']} non-manifold edges ({data['boundary_edges']} boundary)")
                reports.append(report)
                print("[Nexo UV]", report)
            return "; ".join(reports)
        return _run(self, context, action)


class NEXOUV_OT_segment(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.segment"
    bl_label = "Analyze Parts"
    bl_description = "Store deterministic face regions in the nexo_part mesh attribute"

    def execute(self, context):
        def action(objects, s):
            count = 0
            for obj in objects:
                labels, n = mesh_tools.segment_mesh(obj, s.segment_mode, s.seam_angle, s.respect_materials)
                mesh_tools.persist_part_labels(obj, labels)
                count += n
            return f"{count} parts across {len(objects)} mesh(es)"
        return _run(self, context, action)


class NEXOUV_OT_show_parts(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.show_parts"
    bl_label = "Show / Hide Part Colors"
    bl_description = "Toggle reversible vertex-color preview in Solid shading; original materials stay intact"

    def execute(self, context):
        return _run(self, context, lambda objects, s: part_preview.toggle(context, objects))


def _plan(obj, s, style=None):
    data = mesh_tools.analyze_mesh(obj)
    if data['degenerate_faces']:
        raise ValueError(f"{obj.name}: {data['degenerate_faces']} zero-area faces; repair them before unwrapping")
    labels = mesh_tools.read_part_labels(obj) if s.use_parts else None
    previous = [edge.use_seam for edge in obj.data.edges] if s.selected_only else None
    result = mesh_tools.plan_seams(obj, style or s.style, s.seam_angle,
                                  s.respect_existing, labels, s.symmetry_axis, s.hide_seams)
    if previous is not None:
        touched = {obj.data.loops[li].edge_index for face in obj.data.polygons
                   if face.select and not face.hide for li in face.loop_indices}
        for edge in obj.data.edges:
            if edge.index not in touched:
                edge.use_seam = previous[edge.index]
        result = sum(edge.use_seam for edge in obj.data.edges)
    return result


class NEXOUV_OT_preview_seams(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.preview_seams"
    bl_label = "Plan Seams"
    bl_description = "Mark planned seams on the mesh without changing UVs; undo to revert"

    def execute(self, context):
        def action(objects, s):
            return f"{sum(_plan(obj, s) for obj in objects)} seam edges; UVs unchanged"
        return _run(self, context, action)


class NEXOUV_OT_garment_seams(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.garment_seams"
    bl_label = "Garment Seams"
    bl_description = "Plan boundary-aware longitudinal cuts for cloth; inspect before unwrapping"

    def execute(self, context):
        def action(objects, s):
            return f"{sum(_plan(obj, s, 'CLOTH') for obj in objects)} cloth seam edges"
        return _run(self, context, action)


def _unwrap_action(context, objects, s, smart=False):
    supported = bpy.ops.uv.unwrap.get_rna_type().properties['method'].enum_items.keys()
    if not smart and s.unwrap_method not in supported:
        raise ValueError("This Blender version has no Minimum Stretch solver; choose Angle Based or Conformal")
    for obj in objects:
        layer = ensure_uv(obj, s.uv_name, s.backup_uv)
        pinned = {i: tuple(layer.uv[i].vector) for i, item in enumerate(layer.pin) if item.value}
        if not smart:
            _plan(obj, s)
        with edit_mesh(context, obj, s.selected_only):
            if smart:
                _checked(bpy.ops.uv.smart_project(angle_limit=s.smart_angle, island_margin=s.margin,
                                                  correct_aspect=True, scale_to_bounds=False), "Smart UV")
            else:
                _checked(bpy.ops.uv.unwrap(method=s.unwrap_method, margin=s.margin,
                                           fill_holes=True, correct_aspect=True), "Unwrap")
        # Smart projection ignores UV pins; explicitly keep their coordinates.
        layer = obj.data.uv_layers.active
        for i, uv in pinned.items():
            layer.uv[i].vector = uv
        if s.orient_after:
            uv_tools.orient_islands(obj, s.orient_axis, s.selected_only)
    islands = _combined_pack(objects, s, normalize=s.average_density) if s.pack_after else sum(
        len(uv_tools.uv_islands(obj, s.selected_only)) for obj in objects)
    return f"{'Smart UV' if smart else 'Unwrap'}: {len(objects)} mesh(es), {islands} islands"


class NEXOUV_OT_unwrap(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.unwrap"
    bl_label = "Unwrap"
    bl_description = "Plan seams, unwrap with Blender, and pack the selected meshes into one atlas"

    def execute(self, context):
        return _run(self, context, lambda objects, s: _unwrap_action(context, objects, s))


class NEXOUV_OT_smart_project(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.smart_project"
    bl_label = "Smart UV · Hard Surface"
    bl_description = "Use Blender Smart UV Project with backups and a shared multi-object atlas"

    def execute(self, context):
        return _run(self, context, lambda objects, s: _unwrap_action(context, objects, s, True))


class NEXOUV_OT_pack(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.pack"
    bl_label = "Repack / UDIM"
    bl_description = "Deterministic bounding-box packing across selected objects; pinned islands stay fixed"

    def execute(self, context):
        def action(objects, s):
            for obj in objects:
                _active_uv(obj, s)
            return f"Packed {_combined_pack(objects, s)} islands into {s.udim_tiles} tile(s)"
        return _run(self, context, action)


class NEXOUV_OT_relax(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.relax"
    bl_label = "Relax UVs"
    bl_description = "Minimize stretch with Blender while respecting pinned UV coordinates"

    def execute(self, context):
        def action(objects, s):
            for obj in objects:
                layer = _active_uv(obj, s)
                pinned = {i: tuple(layer.uv[i].vector) for i, item in enumerate(layer.pin) if item.value}
                with edit_mesh(context, obj, s.selected_only):
                    _checked(bpy.ops.uv.minimize_stretch(fill_holes=True, blend=0.0,
                                                       iterations=s.relax_iterations), "Relax")
                layer = obj.data.uv_layers.active
                for i, uv in pinned.items():
                    layer.data[i].uv = uv
            return f"Relaxed {len(objects)} mesh(es); pins preserved"
        return _run(self, context, action)


class NEXOUV_OT_orient(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.orient"
    bl_label = "Orient Islands"

    def execute(self, context):
        def action(objects, s):
            count = 0
            for obj in objects:
                _active_uv(obj, s)
                count += uv_tools.orient_islands(obj, s.orient_axis, s.selected_only)
            return f"Oriented {count} unpinned islands"
        return _run(self, context, action)


class NEXOUV_OT_stack(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.stack"
    bl_label = "Stack Repeated Parts"
    bl_description = "Stack exact translated/scaled disconnected duplicates within each mesh; run after packing"

    def execute(self, context):
        def action(objects, s):
            if s.selected_only:
                raise ValueError("Stack works on whole disconnected components; turn off Selected Faces Only")
            count = 0
            for obj in objects:
                _active_uv(obj, s)
                count += uv_tools.stack_repeats(obj, s.symmetry_tolerance)
            return f"Stacked {count} repeated component(s); run stacking after packing"
        return _run(self, context, action)


class NEXOUV_OT_mirror(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.mirror"
    bl_label = "Stack Symmetry"
    bl_description = "Match faces across a local symmetry plane and stack their UVs"

    def execute(self, context):
        def action(objects, s):
            if s.selected_only:
                raise ValueError("Symmetry pairs whole meshes; turn off Selected Faces Only")
            count = 0
            for obj in objects:
                _active_uv(obj, s)
                count += uv_tools.mirror_uv(obj, s.symmetry_axis, s.symmetry_offset, s.symmetry_tolerance)
            return f"Stacked {count} mirrored island(s); unmatched faces kept"
        return _run(self, context, action)


class NEXOUV_OT_quality(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.quality"
    bl_label = "UV Quality Report"
    bl_description = "Measure islands, flipped/zero-area triangles and normalized area stretch"

    def execute(self, context):
        def action(objects, s):
            reports = []
            for obj in objects:
                _active_uv(obj, s, False)
                data = uv_tools.uv_quality(obj)
                reports.append(f"{obj.name}: {data['islands']} islands, {data['flipped_triangles']} flipped, "
                               f"{data['zero_area_triangles']} flat, max stretch {data['max_stretch']:.2f}")
                print("[Nexo UV] quality", obj.name, data)
            for area in context.screen.areas if context.screen else []:
                if area.type == 'IMAGE_EDITOR':
                    area.spaces.active.uv_editor.show_stretch = True
            return "; ".join(reports)
        return _run(self, context, action)


class NEXOUV_OT_match_seams(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.match_seams"
    bl_label = "Seams From Existing UV"

    def execute(self, context):
        def action(objects, s):
            count = 0
            for obj in objects:
                layer = _active_uv(obj, s, False)
                edge_uvs = {}
                for face in obj.data.polygons:
                    loops = list(face.loop_indices)
                    for i, li in enumerate(loops):
                        lj = loops[(i + 1) % len(loops)]
                        vi = obj.data.loops[li].vertex_index
                        vj = obj.data.loops[lj].vertex_index
                        coords = {vi: tuple(layer.data[li].uv), vj: tuple(layer.data[lj].uv)}
                        edge_uvs.setdefault(obj.data.loops[li].edge_index, []).append(coords)
                for edge in obj.data.edges:
                    faces = edge_uvs.get(edge.index, [])
                    split = len(faces) != 2 or any(
                        abs(faces[0][v][axis] - faces[1][v][axis]) > 1e-6
                        for v in edge.vertices for axis in (0, 1))
                    edge.use_seam = split or (s.respect_existing and edge.use_seam)
                    count += edge.use_seam
            return f"{count} seams matched to UV borders"
        return _run(self, context, action)


class NEXOUV_OT_select_island(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.select_island"
    bl_label = "Expand to UV Islands"

    def execute(self, context):
        def action(objects, s):
            count = 0
            for obj in objects:
                _active_uv(obj, s, False)
                for island in uv_tools.uv_islands(obj):
                    if any(obj.data.polygons[i].select for i in island):
                        for i in island:
                            obj.data.polygons[i].select = True
                        count += 1
            return f"Selected {count} islands"
        return _run(self, context, action, selection=True)


class NEXOUV_OT_join_islands(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.join_islands"
    bl_label = "Join Selected Islands"
    bl_description = "Remove seams inside a selected open face patch, then re-unwrap that patch"

    def execute(self, context):
        def action(objects, s):
            count = 0
            for obj in objects:
                _active_uv(obj, s)
                chosen = {p.index for p in obj.data.polygons if p.select and not p.hide}
                if len(chosen) < 2:
                    raise ValueError(f"{obj.name}: select at least two adjacent faces or islands")
                edge_faces = {}
                for face in obj.data.polygons:
                    for li in face.loop_indices:
                        edge_faces.setdefault(obj.data.loops[li].edge_index, []).append(face.index)
                if not any(len([f for f in faces if f in chosen]) == 1 for faces in edge_faces.values()):
                    raise ValueError("Select an open patch; closed surfaces need at least one cut")
                for edge in obj.data.edges:
                    faces = edge_faces.get(edge.index, [])
                    if len(faces) == 2 and all(fi in chosen for fi in faces):
                        count += edge.use_seam
                        edge.use_seam = False
                    elif any(fi in chosen for fi in faces):
                        edge.use_seam = True
                with edit_mesh(context, obj, True):
                    _checked(bpy.ops.uv.unwrap(method='ANGLE_BASED', fill_holes=True,
                                               margin=s.margin), "Join islands")
            return f"Joined across {count} seams; selected patch re-unwrapped"
        return _run(self, context, action)


class NEXOUV_OT_merge_parts(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.merge_parts"
    bl_label = "Merge Selected Parts"
    bl_description = "Merge every part touched by selected faces, preserving full regions"

    def execute(self, context):
        def action(objects, s):
            count = 0
            for obj in objects:
                labels = mesh_tools.read_part_labels(obj)
                if labels is None:
                    raise ValueError("Analyze parts first")
                touched = {labels[p.index] for p in obj.data.polygons if p.select and not p.hide}
                if len(touched) < 2:
                    continue
                target = min(touched)
                labels = [target if v in touched else v for v in labels]
                mesh_tools.persist_part_labels(obj, labels)
                count += len(touched) - 1
            return f"Merged {count} part boundaries"
        return _run(self, context, action)


class NEXOUV_OT_new_part(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.new_part"
    bl_label = "Selection As New Part"

    def execute(self, context):
        def action(objects, s):
            count = 0
            for obj in objects:
                labels = mesh_tools.read_part_labels(obj) or [0] * len(obj.data.polygons)
                new_id = max(labels, default=-1) + 1
                picked = [p.index for p in obj.data.polygons if p.select and not p.hide]
                for index in picked:
                    labels[index] = new_id
                if picked:
                    mesh_tools.persist_part_labels(obj, labels)
                    s.part_id = new_id
                    count += 1
            return f"Created {count} part(s)"
        return _run(self, context, action)


class NEXOUV_OT_reset_parts(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.reset_parts"
    bl_label = "Reset Parts"

    def execute(self, context):
        def action(objects, s):
            for obj in objects:
                attr = obj.data.attributes.get('nexo_part')
                if attr:
                    obj.data.attributes.remove(attr)
            return "Part labels cleared; geometry unchanged"
        return _run(self, context, action)


class NEXOUV_OT_select_part(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.select_part"
    bl_label = "Select Part"

    def execute(self, context):
        def action(objects, s):
            count = sum(mesh_tools.select_part(obj, s.part_id) for obj in objects)
            return f"Selected {count} faces in part {s.part_id}"
        return _run(self, context, action, selection=True)


class NEXOUV_OT_restore_uv(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.restore_uv"
    bl_label = "Restore UV Backup"
    bl_description = "Restore the retained first backup into the named UV map; backup remains available"

    def execute(self, context):
        def action(objects, s):
            for obj in objects:
                name = s.uv_name.strip()
                backup = obj.data.uv_layers.get(backup_name(name))
                if backup is None:
                    raise ValueError(f"{obj.name}: no backup for '{name}'")
                values = read_uv(backup)
                layer = ensure_uv(obj, name, False)
                write_uv(layer, values)
            return f"Restored {len(objects)} UV backup(s)"
        return _run(self, context, action)


class NEXOUV_OT_bake(_MeshAction, bpy.types.Operator):
    bl_idname = "nexo_uv.bake"
    bl_label = "Bake Texture"
    bl_description = "Bake the active object into a new image with CPU Cycles; keep original materials/settings"

    def execute(self, context):
        def action(objects, s):
            obj = context.view_layer.objects.active
            if obj not in objects:
                raise ValueError("The active object must be a mesh")
            _active_uv(obj, s, False)
            image = bake_tools.bake_texture(context, obj, s.bake_type, int(s.bake_size),
                                             s.bake_margin, s.bake_samples)
            for area in context.screen.areas if context.screen else []:
                if area.type == 'IMAGE_EDITOR':
                    area.spaces.active.image = image
            return f"Baked {image.name}; save it from the Image Editor"
        return _run(self, context, action)


CLASSES = (NEXOUV_OT_analyze, NEXOUV_OT_segment, NEXOUV_OT_show_parts, NEXOUV_OT_preview_seams,
           NEXOUV_OT_garment_seams, NEXOUV_OT_unwrap, NEXOUV_OT_smart_project,
           NEXOUV_OT_pack, NEXOUV_OT_relax, NEXOUV_OT_orient, NEXOUV_OT_stack,
           NEXOUV_OT_mirror, NEXOUV_OT_quality, NEXOUV_OT_match_seams,
           NEXOUV_OT_select_island, NEXOUV_OT_join_islands, NEXOUV_OT_merge_parts, NEXOUV_OT_new_part,
           NEXOUV_OT_reset_parts, NEXOUV_OT_select_part, NEXOUV_OT_restore_uv,
           NEXOUV_OT_bake)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    part_preview.restore_all()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
