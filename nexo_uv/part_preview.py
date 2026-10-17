# SPDX-License-Identifier: GPL-3.0-or-later
"""Reversible part colors in the Solid viewport, without material edits."""

from array import array
import colorsys


_ATTRIBUTE_NAME = ".NexoUV Preview"
_mesh_states = []
_space_states = []


def _color(part):
    # Multiplication by the golden ratio spreads adjacent IDs around the wheel.
    hue = (int(part) * 0.6180339887498949 + 0.07) % 1.0
    return (*colorsys.hsv_to_rgb(hue, 0.65, 0.9), 1.0)


def _workspace_views(context):
    workspace = getattr(context, "workspace", None)
    screens = list(workspace.screens) if workspace is not None else []
    if not screens:
        screen = getattr(context, "screen", None)
        screens = [screen] if screen is not None else []
    seen = set()
    for screen in screens:
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            if space.type == "VIEW_3D" and space.as_pointer() not in seen:
                seen.add(space.as_pointer())
                yield area, space


def _restore_mesh(state):
    mesh = state["mesh"]
    try:
        colors = mesh.color_attributes
        # Both name and RNA identity must match. Blender may reuse an attribute
        # pointer after deletion, and other RNA wrappers can then point at the
        # replacement. A renamed preview is treated as a user-adopted layer.
        try:
            owned_pointer = state["owned_attribute"].as_pointer()
        except ReferenceError:
            owned_pointer = 0
        owned = next(
            (attribute for attribute in colors
             if attribute.as_pointer() == owned_pointer
             and attribute.name == state["owned_name"]),
            None,
        )
        if owned is not None:
            colors.remove(owned)
        previous_color = colors.get(state["active_color"])
        colors.active_color = previous_color
        previous_default = state["default_color"]
        if not previous_default or colors.get(previous_default) is not None:
            colors.default_color_name = previous_default
        previous_attribute = mesh.attributes.get(state["active_attribute"])
        mesh.attributes.active = previous_attribute
        mesh.update()
    except (ReferenceError, RuntimeError, AttributeError):
        # Deleting an object, mesh, or attribute during preview is supported.
        pass


def restore_all():
    """Restore every modified mesh and viewport; safe to call repeatedly."""
    mesh_states = list(_mesh_states)
    space_states = list(_space_states)
    _mesh_states.clear()
    _space_states.clear()
    for state in mesh_states:
        _restore_mesh(state)
    for state in space_states:
        try:
            state["space"].shading.color_type = state["color_type"]
            state["area"].tag_redraw()
        except (ReferenceError, RuntimeError, AttributeError):
            pass


def toggle(context, objects):
    """Toggle deterministic part colors and return a user-facing status.

    Only ``shading.color_type`` is changed. A Material/Rendered viewport keeps
    its shading mode; the status explains that Solid is needed for these colors.
    Meshes shared by multiple objects receive a single temporary color layer.
    """
    if _mesh_states or _space_states:
        restore_all()
        return "Part colors hidden; previous viewport colors restored."

    meshes = []
    seen = set()
    for obj in objects:
        if getattr(obj, "type", None) != "MESH":
            continue
        mesh = obj.data
        pointer = mesh.as_pointer()
        if pointer in seen:
            continue
        seen.add(pointer)
        attribute = mesh.attributes.get("nexo_part")
        if (attribute is None or attribute.domain != "FACE"
                or attribute.data_type != "INT"
                or len(attribute.data) != len(mesh.polygons)):
            raise ValueError("Analyze Parts first to show part colors.")
        labels = [0] * len(attribute.data)
        attribute.data.foreach_get("value", labels)
        meshes.append((mesh, labels))
    if not meshes:
        raise ValueError("Choose a mesh object, then Analyze Parts first.")

    needs_solid = False
    views = list(_workspace_views(context))
    try:
        for mesh, labels in meshes:
            colors = mesh.color_attributes
            previous_color = colors.active_color
            previous_attribute = mesh.attributes.active
            state = {
                "mesh": mesh,
                "active_color": previous_color.name if previous_color else "",
                "default_color": colors.default_color_name,
                "active_attribute": previous_attribute.name if previous_attribute else "",
                "owned_attribute": None,
                "owned_name": "",
            }
            # Blender chooses a fresh suffix when this name already exists.
            preview = colors.new(_ATTRIBUTE_NAME, "BYTE_COLOR", "CORNER")
            state["owned_attribute"] = preview
            state["owned_name"] = preview.name
            _mesh_states.append(state)
            palette = {part: array("f", _color(part)) for part in set(labels)}
            values = array("f", [0.0]) * (len(mesh.loops) * 4)
            for polygon, part in zip(mesh.polygons, labels):
                start = polygon.loop_start * 4
                end = start + polygon.loop_total * 4
                values[start:end] = palette[part] * polygon.loop_total
            preview.data.foreach_set("color", values)
            colors.active_color = preview
            mesh.update()

        for area, space in views:
            _space_states.append({
                "area": area,
                "space": space,
                "color_type": space.shading.color_type,
            })
            needs_solid = needs_solid or space.shading.type != "SOLID"
            space.shading.color_type = "VERTEX"
            area.tag_redraw()
    except Exception:
        restore_all()
        raise

    if not views or needs_solid:
        return "Part colors enabled. Switch the 3D View to Solid shading to see them."
    return "Part colors shown. Toggle again to restore previous viewport colors."
