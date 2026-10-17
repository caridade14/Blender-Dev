# SPDX-License-Identifier: GPL-3.0-or-later
import math
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty


class NEXOUV_Settings(bpy.types.PropertyGroup):
    style: EnumProperty(name="Surface", items=[
        ('HARD_SURFACE', "Hard Surface", "Split at sharp angles and part borders"),
        ('ORGANIC', "Organic", "Plan graph cuts for curved, closed surfaces"),
        ('CLOTH', "Cloth / Garment", "Boundary-aware longitudinal cuts; no garment classification")])
    uv_name: StringProperty(name="UV Map", default="NexoUV")
    backup_uv: BoolProperty(name="Back Up Existing UVs", default=True,
                           description="Keep one recoverable copy before replacing this UV map")
    selected_only: BoolProperty(name="Selected Faces Only", default=False,
                               description="Use the mesh face selection, including in Object Mode")
    respect_existing: BoolProperty(name="Keep Existing Seams", default=True)
    seam_angle: FloatProperty(name="Crease Angle", default=math.radians(55), min=0.01,
                             max=math.pi, subtype='ANGLE')
    hide_seams: BoolProperty(name="Prefer Back Seams", default=True,
                            description="Prefer the negative local Y side for graph cuts")
    segment_mode: EnumProperty(name="Parts From", items=[
        ('ANGLE', "Creases", "Connected faces separated by sharp edges"),
        ('CONNECTED', "Connectivity", "One part per connected component"),
        ('MATERIAL', "Materials", "Connected material regions"),
        ('SEAMS', "Seams", "Connected regions bounded by marked seams")])
    respect_materials: BoolProperty(name="Respect Material Borders", default=True)
    use_parts: BoolProperty(name="Cut at Part Borders", default=True)
    part_id: IntProperty(name="Part", min=0, default=0)
    unwrap_method: EnumProperty(name="Solver", items=[
        ('ANGLE_BASED', "Angle Based", "Blender angle-based flattening"),
        ('CONFORMAL', "Conformal", "Blender least-squares conformal mapping"),
        ('MINIMUM_STRETCH', "Minimum Stretch (4.3+)", "Available on Blender 4.3 and newer")])
    smart_angle: FloatProperty(name="Projection Angle", default=math.radians(66),
                              min=0.01, max=math.pi / 2, subtype='ANGLE')
    pack_after: BoolProperty(name="Pack After Unwrap", default=True)
    margin: FloatProperty(name="Tile Margin", min=0.001, max=0.1, default=0.01, precision=3,
                          description="UV units; also used as minimum spacing between island bounds")
    udim_tiles: IntProperty(name="UDIM Tiles", min=1, max=100, default=1)
    preserve_density: BoolProperty(name="Preserve Current UV Scale", default=False,
                                 description="Reject packing if current island sizes do not fit")
    average_density: BoolProperty(name="Normalize Texel Density", default=True,
                                description="Average island scale before packing after unwrap")
    orient_after: BoolProperty(name="Orient After Unwrap", default=False)
    orient_axis: EnumProperty(name="UV Long Axis", items=[('X', "U", "Align to U"),
                                                        ('Y', "V", "Align to V")])
    symmetry_axis: EnumProperty(name="Mirror Axis", items=[
        ('X', "X", "Local X plane"), ('Y', "Y", "Local Y plane"), ('Z', "Z", "Local Z plane")])
    symmetry_offset: FloatProperty(name="Plane Offset", default=0.0)
    symmetry_tolerance: FloatProperty(name="Match Tolerance", min=0.000001, default=0.0001,
                                     precision=5)
    relax_iterations: IntProperty(name="Iterations", min=1, max=500, default=20)
    bake_type: EnumProperty(name="Bake", items=[('AO', "Ambient Occlusion", "Cycles AO"),
        ('NORMAL', "Tangent Normal", "Cycles tangent-space normals"),
        ('DIFFUSE', "Base Color", "Diffuse color without lighting"),
        ('EMIT', "Emission", "Emission from materials")])
    bake_size: EnumProperty(name="Resolution", items=[(str(n), str(n), "")
                           for n in (64, 256, 512, 1024, 2048, 4096, 8192)], default='1024')
    bake_samples: IntProperty(name="Samples", min=1, max=256, default=16)
    bake_margin: IntProperty(name="Padding (px)", min=0, max=64, default=8)
    status: StringProperty(name="Last Result", default="Select a mesh to begin")
