# SPDX-License-Identifier: GPL-3.0-or-later
"""State ownership and rollback for mesh operations. No global context overrides."""
from contextlib import contextmanager
import hashlib
import bpy


def read_uv(layer):
    """Snapshot UV CustomData through current attribute API (Blender 4.2+).

    The legacy MeshUVLoop flags can allocate new CustomData attributes and
    invalidate a live iterator; bulk access avoids stale RNA pointers.
    """
    mesh, name = layer.id_data, layer.name
    n = len(mesh.uv_layers[name].uv)
    coords, pins, selected, edges = [0.0] * (n * 2), [False] * n, [False] * n, [False] * n
    mesh.uv_layers[name].uv.foreach_get('vector', coords)
    mesh.uv_layers[name].pin.foreach_get('value', pins)
    mesh.uv_layers[name].vertex_selection.foreach_get('value', selected)
    mesh.uv_layers[name].edge_selection.foreach_get('value', edges)
    return coords, pins, selected, edges


def write_uv(layer, values):
    mesh, name = layer.id_data, layer.name
    coords, pins, selected, edges = values
    mesh.uv_layers[name].uv.foreach_set('vector', coords)
    mesh.uv_layers[name].pin.foreach_set('value', pins)
    mesh.uv_layers[name].vertex_selection.foreach_set('value', selected)
    mesh.uv_layers[name].edge_selection.foreach_set('value', edges)


def _uv_snapshot(mesh):
    metadata = [(l.name, l.active_render, l.active_clone) for l in mesh.uv_layers]
    return [(name, read_uv(mesh.uv_layers[name]), render, clone)
            for name, render, clone in metadata]


def _restore_uv(mesh, layers, active):
    while mesh.uv_layers:
        mesh.uv_layers.remove(mesh.uv_layers[-1])
    for name, values, render, clone in layers:
        layer = mesh.uv_layers.new(name=name, do_init=False)
        write_uv(layer, values)
    if layers:
        # Setting a later layer's flag False can clear the earlier True flag.
        # Choose the saved owners once after all CustomData has been created.
        for name, _, render, clone in layers:
            if render:
                mesh.uv_layers[name].active_render = True
            if clone:
                mesh.uv_layers[name].active_clone = True
        mesh.uv_layers.active_index = min(active, len(layers) - 1)


class MeshSnapshot:
    def __init__(self, obj):
        mesh = obj.data
        self.obj = obj
        self.layers = _uv_snapshot(mesh)
        self.active = mesh.uv_layers.active_index
        self.seams = [edge.use_seam for edge in mesh.edges]
        self.selection = ([v.select for v in mesh.vertices], [e.select for e in mesh.edges],
                          [p.select for p in mesh.polygons])
        attr = mesh.attributes.get("nexo_part")
        if attr and (attr.domain != 'FACE' or attr.data_type != 'INT'):
            raise ValueError(f"{obj.name}: attribute 'nexo_part' must be Face / Integer; rename the conflicting attribute")
        self.parts = [i.value for i in attr.data] if attr else None

    def restore_selection(self):
        for elements, values in zip((self.obj.data.vertices, self.obj.data.edges,
                                    self.obj.data.polygons), self.selection):
            for item, selected in zip(elements, values):
                item.select = selected

    def rollback(self):
        mesh = self.obj.data
        _restore_uv(mesh, self.layers, self.active)
        for edge, value in zip(mesh.edges, self.seams):
            edge.use_seam = value
        attr = mesh.attributes.get("nexo_part")
        if self.parts is None:
            if attr:
                mesh.attributes.remove(attr)
        else:
            if not attr:
                attr = mesh.attributes.new("nexo_part", 'INT', 'FACE')
            for item, value in zip(attr.data, self.parts):
                item.value = value
        mesh.update()


@contextmanager
def mesh_session(context, *, rollback=True, keep_selection=False):
    """Isolate operations and restore mode/selection, rolling back data on failure.

    Geometry topology is never modified. Linked mesh data is rejected rather than
    silently changing unselected instances. Selection tools explicitly keep their
    new face selection; all other tools restore the user's selection.
    """
    active = context.view_layer.objects.active
    selected = list(context.selected_objects)
    previous_mode = active.mode if active else 'OBJECT'
    if previous_mode not in {'OBJECT', 'EDIT'}:
        raise ValueError("Switch to Object or Edit Mode before using Nexo UV")
    if previous_mode == 'EDIT':
        bpy.ops.object.mode_set(mode='OBJECT')
    objects = [o for o in selected if o.type == 'MESH' and o.data.polygons]
    snapshots = []
    try:
        if not objects:
            raise ValueError("Select at least one mesh with faces")
        for obj in objects:
            if not obj.is_editable or not obj.data.is_editable:
                raise ValueError(f"{obj.name}: linked data is read-only; make a local copy")
            if obj.data.users > 1:
                raise ValueError(f"{obj.name}: mesh is shared; use Object > Relations > Make Single User > Object & Data")
        snapshots = [MeshSnapshot(obj) for obj in objects]
        yield objects
    except Exception:
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if rollback:
            for snapshot in snapshots:
                snapshot.rollback()
        raise
    finally:
        if context.object and context.object.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
        if not keep_selection:
            for snapshot in snapshots:
                snapshot.restore_selection()
        for obj in list(context.selected_objects):
            obj.select_set(False)
        for obj in selected:
            if obj.name in context.view_layer.objects:
                obj.select_set(True)
        context.view_layer.objects.active = active
        if previous_mode == 'EDIT' and active:
            bpy.ops.object.mode_set(mode='EDIT')


@contextmanager
def edit_mesh(context, obj, selected_only=False):
    """Run Blender UV operators on exactly one mesh, with synchronized selection."""
    for other in list(context.selected_objects):
        other.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj
    if selected_only and not any(p.select and not p.hide for p in obj.data.polygons):
        raise ValueError(f"{obj.name}: select some visible faces first")
    settings = context.scene.tool_settings
    old_sync = settings.use_uv_select_sync
    old_select_mode = tuple(settings.mesh_select_mode)
    settings.use_uv_select_sync = True
    settings.mesh_select_mode = (False, False, True)
    bpy.ops.object.mode_set(mode='EDIT')
    try:
        if not selected_only:
            bpy.ops.mesh.select_all(action='SELECT')
        yield
    finally:
        if obj.mode == 'EDIT':
            bpy.ops.object.mode_set(mode='OBJECT')
        settings.use_uv_select_sync = old_sync
        settings.mesh_select_mode = old_select_mode


def backup_name(name):
    """Fit Blender's 63-byte attribute name limit without ambiguous truncation."""
    result = name + '__backup'
    if len(result.encode('utf-8')) > 63:
        prefix = name.encode('utf-8')[:46].decode('utf-8', errors='ignore')
        result = prefix + '_' + hashlib.sha256(name.encode('utf-8')).hexdigest()[:8] + '__backup'
    return result


def ensure_uv(obj, name, backup=True):
    name = name.strip()
    if not name:
        raise ValueError("UV map name cannot be empty")
    if len(name.encode('utf-8')) > 63:
        raise ValueError("UV map names must fit Blender's 63-byte UTF-8 limit")
    mesh = obj.data
    render_name = next((l.name for l in mesh.uv_layers if l.active_render), None)
    clone_name = next((l.name for l in mesh.uv_layers if l.active_clone), None)
    layer = mesh.uv_layers.get(name)
    if layer is not None and backup:
        copy_name = backup_name(name)
        # Retain the first backup until explicitly restored/deleted.
        if mesh.uv_layers.get(copy_name) is None:
            if len(mesh.uv_layers) >= 8:
                raise ValueError(f"{obj.name}: UV layer limit reached; remove an unused layer")
            original = read_uv(layer)
            copy = mesh.uv_layers.new(name=copy_name, do_init=False)
            if copy.name != copy_name:
                mesh.uv_layers.remove(copy)
                raise ValueError(f"Attribute name '{copy_name}' is already in use; rename that attribute")
            write_uv(copy, original)
            # Adding a UV layer can invalidate earlier RNA references.
            layer = mesh.uv_layers.get(name)
    if layer is None:
        if len(mesh.uv_layers) >= 8:
            raise ValueError(f"{obj.name}: UV layer limit reached; remove an unused layer")
        layer = mesh.uv_layers.new(name=name, do_init=True)
        if layer.name != name:
            mesh.uv_layers.remove(layer)
            raise ValueError(f"Attribute name '{name}' is already in use; rename that attribute")
        n = len(mesh.loops)
        # A new map never inherits accidental selection/pin flags.
        layer.pin.foreach_set('value', [False] * n)
        layer.vertex_selection.foreach_set('value', [False] * n)
        layer.edge_selection.foreach_set('value', [False] * n)
    layer = mesh.uv_layers[name]
    mesh.uv_layers.active_index = mesh.uv_layers.find(name)
    if render_name:
        mesh.uv_layers[render_name].active_render = True
    if clone_name:
        mesh.uv_layers[clone_name].active_clone = True
    return layer
