# SPDX-License-Identifier: GPL-3.0-or-later
"""Native Blender sidebar; no custom GPU draw handlers or external links."""
from pathlib import Path
import textwrap
import bpy
import bpy.utils.previews

_previews = None


class NEXOUV_PT_main(bpy.types.Panel):
    bl_idname = 'NEXOUV_PT_main'
    bl_label = 'Nexo UV'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Nexo UV'

    def draw(self, context):
        layout = self.layout
        s = context.scene.nexo_uv
        row = layout.row()
        if _previews and 'logo' in _previews:
            row.template_icon(icon_value=_previews['logo'].icon_id, scale=3.0)
        column = row.column()
        column.label(text='NEXO UV', icon='UV')
        column.label(text='Unfold. Refine. Create.')
        layout.prop(s, 'uv_name')
        layout.prop(s, 'style')
        layout.prop(s, 'selected_only')
        row = layout.row(align=True)
        row.scale_y = 1.6
        row.operator('nexo_uv.unwrap', icon='UV')
        layout.operator('nexo_uv.smart_project', icon='MOD_UVPROJECT')
        row = layout.row(align=True)
        row.operator('nexo_uv.preview_seams', text='Plan Seams', icon='EDGESEL')
        row.operator('nexo_uv.garment_seams', text='Garment')
        layout.operator('nexo_uv.analyze', icon='VIEWZOOM')
        box = layout.box()
        box.label(text='Last result', icon='INFO')
        width = max(20, int(context.region.width / 7)) if context.region else 35
        for line in textwrap.wrap(s.status, width=width)[:8]:
            box.label(text=line)


class _Sub:
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Nexo UV'
    bl_parent_id = 'NEXOUV_PT_main'
    bl_options = {'DEFAULT_CLOSED'}


class NEXOUV_PT_parts(_Sub, bpy.types.Panel):
    bl_idname = 'NEXOUV_PT_parts'
    bl_label = 'Parts'

    def draw(self, context):
        l, s = self.layout, context.scene.nexo_uv
        l.prop(s, 'segment_mode')
        l.prop(s, 'respect_materials')
        l.operator('nexo_uv.segment', icon='OUTLINER_OB_MESH')
        l.operator('nexo_uv.show_parts', icon='COLOR')
        row = l.row(align=True)
        row.prop(s, 'part_id')
        row.operator('nexo_uv.select_part', text='Select')
        l.operator('nexo_uv.merge_parts')
        l.operator('nexo_uv.new_part')
        l.operator('nexo_uv.reset_parts', icon='LOOP_BACK')


class NEXOUV_PT_seams(_Sub, bpy.types.Panel):
    bl_idname = 'NEXOUV_PT_seams'
    bl_label = 'Seams & Solver'

    def draw(self, context):
        l, s = self.layout, context.scene.nexo_uv
        l.prop(s, 'unwrap_method')
        l.prop(s, 'seam_angle')
        l.prop(s, 'respect_existing')
        l.prop(s, 'use_parts')
        l.prop(s, 'hide_seams')
        l.prop(s, 'smart_angle')
        l.operator('nexo_uv.match_seams', icon='EDGESEL')


class NEXOUV_PT_refine(_Sub, bpy.types.Panel):
    bl_idname = 'NEXOUV_PT_refine'
    bl_label = 'Refine Islands'

    def draw(self, context):
        l, s = self.layout, context.scene.nexo_uv
        l.operator('nexo_uv.select_island', icon='UV_ISLANDSEL')
        l.operator('nexo_uv.join_islands', icon='AUTOMERGE_ON')
        l.label(text='Redo: enable Selected Faces Only, then Unwrap')
        row = l.row(align=True)
        row.prop(s, 'relax_iterations')
        row.operator('nexo_uv.relax', text='Relax')
        row = l.row(align=True)
        row.prop(s, 'orient_axis')
        row.operator('nexo_uv.orient', text='Orient')
        l.operator('nexo_uv.quality', icon='CHECKMARK')


class NEXOUV_PT_packing(_Sub, bpy.types.Panel):
    bl_idname = 'NEXOUV_PT_packing'
    bl_label = 'Packing & UDIM'

    def draw(self, context):
        l, s = self.layout, context.scene.nexo_uv
        l.prop(s, 'pack_after')
        l.prop(s, 'margin')
        l.prop(s, 'udim_tiles')
        l.prop(s, 'preserve_density')
        l.prop(s, 'average_density')
        l.prop(s, 'orient_after')
        l.operator('nexo_uv.pack', icon='UV_SYNC_SELECT')
        l.separator()
        l.label(text='Stack after the final pack')
        l.operator('nexo_uv.stack', icon='DUPLICATE')


class NEXOUV_PT_symmetry(_Sub, bpy.types.Panel):
    bl_idname = 'NEXOUV_PT_symmetry'
    bl_label = 'Symmetry'

    def draw(self, context):
        l, s = self.layout, context.scene.nexo_uv
        l.prop(s, 'symmetry_axis', expand=True)
        l.prop(s, 'symmetry_offset')
        l.prop(s, 'symmetry_tolerance')
        l.operator('nexo_uv.mirror', icon='MOD_MIRROR')


class NEXOUV_PT_bake(_Sub, bpy.types.Panel):
    bl_idname = 'NEXOUV_PT_bake'
    bl_label = 'Bake Texture'

    def draw(self, context):
        l, s = self.layout, context.scene.nexo_uv
        l.prop(s, 'bake_type')
        l.prop(s, 'bake_size')
        l.prop(s, 'bake_samples')
        l.prop(s, 'bake_margin')
        l.operator('nexo_uv.bake', icon='RENDER_STILL')
        l.label(text='Active mesh only · CPU Cycles')
        l.label(text='Save the new image from the Image Editor')


class NEXOUV_PT_safety(_Sub, bpy.types.Panel):
    bl_idname = 'NEXOUV_PT_safety'
    bl_label = 'UV Backups'

    def draw(self, context):
        l, s = self.layout, context.scene.nexo_uv
        l.prop(s, 'backup_uv')
        l.operator('nexo_uv.restore_uv', icon='RECOVER_LAST')
        l.label(text='First backup kept as <UV Map>__backup')
        l.label(text='Delete that map manually to refresh it')


CLASSES = (NEXOUV_PT_main, NEXOUV_PT_parts, NEXOUV_PT_seams, NEXOUV_PT_refine,
           NEXOUV_PT_packing, NEXOUV_PT_symmetry, NEXOUV_PT_bake, NEXOUV_PT_safety)


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    logo = Path(__file__).parent / 'assets' / 'logo.png'
    if logo.is_file():
        try:
            _previews.load('logo', str(logo), 'IMAGE')
        except (RuntimeError, OSError):
            pass  # A missing optional preview must not prevent UV tools loading.
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    global _previews
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None
