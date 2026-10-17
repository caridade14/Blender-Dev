# SPDX-License-Identifier: GPL-3.0-or-later
"""Real CPU Cycles bake and restoration checks in a disposable Blender scene.

Run from the repository root (no external packages or assets are needed)::

    blender --background --threads 2 --python-exit-code 1 --python tests/blender_bake_smoke.py

The four successful bake checks call Blender's actual bake operator. Only the
failure-cleanup check substitutes a CANCELLED result, and must never return an
image. A failure exits the process with status 1 even without --python-exit-code.
"""

import math
import os
from pathlib import Path
import sys
import traceback
from types import SimpleNamespace

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nexo_uv import bake_tools  # noqa: E402


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    obj = bpy.context.object
    material = bpy.data.materials.new('UserOriginal')
    material.use_nodes = True
    obj.data.materials.append(material)
    original_nodes = tuple(material.node_tree.nodes)
    bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0))
    other = bpy.context.object
    other.data.materials.append(material)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    scene = bpy.context.scene
    scene.render.bake.use_selected_to_active = True
    scene.render.bake.margin = 3
    scene.render.bake.use_pass_direct = True
    scene.render.bake.use_pass_indirect = True
    scene.cycles.samples = 7
    scene.cycles.device = 'GPU'

    def settings():
        bake = scene.render.bake
        return (scene.render.engine, scene.cycles.samples, scene.cycles.device,
                bake.use_selected_to_active, bake.margin, bake.use_pass_direct,
                bake.use_pass_indirect, bake.use_pass_color)

    original_settings = settings()

    def verify_restore():
        assert settings() == original_settings, (settings(), original_settings)
        assert obj.active_material_index == 0
        assert obj.material_slots[0].material == material
        assert obj.material_slots[0].link == 'DATA'
        assert other.material_slots[0].material == material
        assert tuple(material.node_tree.nodes) == original_nodes
        assert obj.select_get() and other.select_get()
        assert bpy.context.view_layer.objects.active == obj
        assert not any(m.name.startswith('NexoUV_Bake') for m in bpy.data.materials)

    successful_images = set()
    for bake_type in ('AO', 'NORMAL', 'DIFFUSE', 'EMIT'):
        image = bake_tools.bake_texture(
            bpy.context, obj, bake_type=bake_type, size=32, samples=1,
        )
        assert image not in successful_images
        successful_images.add(image)
        assert tuple(image.size) == (32, 32)
        pixels = tuple(image.pixels)
        assert len(pixels) == 32 * 32 * 4
        assert all(math.isfinite(value) for value in pixels)
        if bake_type in {'AO', 'NORMAL', 'DIFFUSE'}:
            assert max(pixels[0::4]) > 0, 'Expected nonzero baked RGB values'
        if bake_type in {'AO', 'NORMAL'}:
            assert image.colorspace_settings.name == 'Non-Color'
        verify_restore()
        print('BAKE_OK', bake_type, image.name, flush=True)

    # A mesh with no material must return to having no slots at all.
    obj.data.materials.clear()
    extra_image = bake_tools.bake_texture(
        bpy.context, obj, bake_type='AO', size=32, samples=1,
    )
    assert extra_image not in successful_images
    assert len(obj.material_slots) == 0
    assert not any(m.name.startswith('NexoUV_Bake') for m in bpy.data.materials)
    obj.data.materials.append(material)
    verify_restore()

    # Reject a cancelled operator and clean its generated image/materials.
    # All datablocks and context remain real Blender data in this branch.
    original_bpy = bake_tools.bpy
    bake_tools.bpy = SimpleNamespace(
        data=bpy.data,
        ops=SimpleNamespace(object=SimpleNamespace(bake=lambda **kwargs: {'CANCELLED'})),
    )
    images_before = set(bpy.data.images)
    try:
        try:
            bake_tools.bake_texture(bpy.context, obj, size=32, samples=1)
        except RuntimeError as exc:
            assert 'did not finish' in str(exc), str(exc)
        else:
            raise AssertionError('A CANCELLED operator must raise')
    finally:
        bake_tools.bpy = original_bpy
    assert set(bpy.data.images) == images_before
    verify_restore()
    print('BAKE_SMOKE_PASSED blender=%s' % bpy.app.version_string, flush=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(1)
