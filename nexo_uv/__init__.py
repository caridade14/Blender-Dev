# SPDX-License-Identifier: GPL-3.0-or-later
"""Nexo UV — independently implemented, Blender-native UV tools."""

bl_info = {
    "name": "Nexo UV",
    "author": "Nexo UV contributors",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Nexo UV",
    "description": "Portable UV workflow: seams, parts, unwrap, UDIM and quality checks",
    "category": "UV",
}

import bpy
from . import operators, ui
from .properties import NEXOUV_Settings


def register():
    bpy.utils.register_class(NEXOUV_Settings)
    bpy.types.Scene.nexo_uv = bpy.props.PointerProperty(type=NEXOUV_Settings)
    operators.register()
    ui.register()


def unregister():
    ui.unregister()
    operators.unregister()
    if hasattr(bpy.types.Scene, "nexo_uv"):
        del bpy.types.Scene.nexo_uv
    bpy.utils.unregister_class(NEXOUV_Settings)
