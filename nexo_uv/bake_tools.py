# SPDX-License-Identifier: GPL-3.0-or-later
"""Portable Cycles texture baking without changing the source materials."""

import bpy


def bake_texture(context, obj, bake_type='AO', size=1024, margin=8, samples=16):
    """Bake one mesh to a new image and restore the user's scene settings.

    The caller supplies an object-mode context and an existing active UV map.
    Images are returned in memory; saving a texture is a separate user action.
    Temporary image nodes live exclusively in private material copies.
    """
    if obj is None or obj.type != 'MESH' or not obj.data.polygons:
        raise ValueError("Select a mesh with faces to bake")
    if obj.mode != 'OBJECT':
        raise ValueError("Texture baking requires Object Mode")
    if obj.data.uv_layers.active is None:
        raise ValueError("The mesh needs an active UV map before baking")
    bake_type = str(bake_type).upper()
    if bake_type not in {'AO', 'NORMAL', 'DIFFUSE', 'EMIT'}:
        raise ValueError("Bake type must be AO, NORMAL, DIFFUSE or EMIT")
    for name, value, minimum in (
        ('size', size, 1), ('margin', margin, 0), ('samples', samples, 1)
    ):
        if isinstance(value, bool) or int(value) != value or value < minimum:
            raise ValueError("%s must be an integer of at least %d" % (name, minimum))
    size, margin, samples = int(size), int(margin), int(samples)

    scene = context.scene
    view_layer = context.view_layer
    render = scene.render
    if not hasattr(scene, 'cycles'):
        raise RuntimeError("Cycles is unavailable in this Blender installation")
    cycles = scene.cycles
    bake = render.bake
    settings = {
        'engine': render.engine,
        'samples': cycles.samples,
        'device': cycles.device,
        'use_selected_to_active': bake.use_selected_to_active,
        'margin': bake.margin,
        'use_pass_direct': bake.use_pass_direct,
        'use_pass_indirect': bake.use_pass_indirect,
        'use_pass_color': bake.use_pass_color,
    }
    active_object = view_layer.objects.active
    selected_objects = tuple(o for o in view_layer.objects if o.select_get())
    active_material_index = obj.active_material_index
    original_slot_count = len(obj.material_slots)
    slot_states = []
    temporary_materials = []
    image = None
    completed = False

    try:
        image = bpy.data.images.new(
            name="NexoUV_%s_%s" % (obj.name, bake_type),
            width=size, height=size, alpha=False, float_buffer=False,
        )
        if bake_type in {'NORMAL', 'AO'}:
            image.colorspace_settings.name = 'Non-Color'

        # An empty material list needs one slot for Blender's bake target.
        if original_slot_count == 0:
            obj.data.materials.append(None)

        for slot in obj.material_slots:
            source = slot.material
            previous_link = slot.link
            # Object-linked copies leave existing shared mesh slots alone.
            # A newly added first slot needs no private object override.
            if original_slot_count:
                slot.link = 'OBJECT'
                previous_object_material = slot.material
                slot_states.append((slot, previous_link, previous_object_material))
            material = source.copy() if source is not None else bpy.data.materials.new(
                "NexoUV_BakeMaterial"
            )
            temporary_materials.append(material)
            material.name = "NexoUV_Bake_%s" % (source.name if source else obj.name)
            material.use_nodes = True
            nodes = material.node_tree.nodes
            for node in nodes:
                node.select = False
            target = nodes.new('ShaderNodeTexImage')
            target.image = image
            target.select = True
            nodes.active = target
            if original_slot_count:
                slot.material = material
            else:
                obj.data.materials[0] = material

        for selected in selected_objects:
            if selected != obj:
                selected.select_set(False)
        obj.select_set(True)
        view_layer.objects.active = obj
        render.engine = 'CYCLES'
        cycles.device = 'CPU'
        cycles.samples = samples
        bake.use_selected_to_active = False
        bake.margin = margin
        bake.use_pass_direct = False
        bake.use_pass_indirect = False
        bake.use_pass_color = True
        view_layer.update()

        result = bpy.ops.object.bake(
            type=bake_type, save_mode='INTERNAL', use_clear=True,
            uv_layer=obj.data.uv_layers.active.name, normal_space='TANGENT',
        )
        if 'FINISHED' not in result:
            raise RuntimeError("Cycles did not finish the %s bake: %s" % (bake_type, result))
        completed = True
        return image
    finally:
        # Keep cleaning after an individual restore error; never leave private
        # materials or modified render settings behind because another failed.
        restore_errors = []

        def restore(action):
            try:
                action()
            except Exception as exc:
                restore_errors.append(str(exc))

        for slot, previous_link, previous_material in slot_states:
            restore(lambda s=slot, m=previous_material: setattr(s, 'material', m))
            restore(lambda s=slot, link=previous_link: setattr(s, 'link', link))
        if original_slot_count == 0 and len(obj.data.materials) > 0:
            # clear() also synchronizes object slot counts; pop() leaves an
            # empty object slot behind in some supported Blender versions.
            restore(lambda: obj.data.materials.clear())
        restore(lambda: setattr(obj, 'active_material_index', active_material_index))
        for material in temporary_materials:
            restore(lambda m=material: bpy.data.materials.remove(m, do_unlink=True))
        for name in ('use_selected_to_active', 'margin', 'use_pass_direct',
                     'use_pass_indirect', 'use_pass_color'):
            restore(lambda key=name: setattr(bake, key, settings[key]))
        restore(lambda: setattr(cycles, 'samples', settings['samples']))
        restore(lambda: setattr(cycles, 'device', settings['device']))
        restore(lambda: setattr(render, 'engine', settings['engine']))
        selected_set = set(selected_objects)
        for candidate in view_layer.objects:
            if candidate.select_get() != (candidate in selected_set):
                restore(lambda o=candidate: o.select_set(o in selected_set))
        restore(lambda: setattr(view_layer.objects, 'active', active_object))
        if image is not None and (not completed or restore_errors):
            restore(lambda: bpy.data.images.remove(image, do_unlink=True))
        if restore_errors:
            raise RuntimeError("Could not completely restore the bake context: "
                               + "; ".join(restore_errors))
