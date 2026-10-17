# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the legacy ZIP installs and runs, isolated from the source checkout.

Set BLENDER_USER_SCRIPTS to a disposable directory, build both ZIPs first, and
run this script in Blender from a directory outside the source repository.
"""
from pathlib import Path
import os
import sys

import addon_utils
import bpy


def main():
    scripts = Path(os.environ['BLENDER_USER_SCRIPTS']).resolve()
    package = Path(__file__).resolve().parents[1] / 'dist' / 'nexo_uv-0.1.0-legacy.zip'
    assert package.is_file(), 'Run python tools/build.py first'
    assert 'FINISHED' in bpy.ops.preferences.addon_install(filepath=str(package), overwrite=True)
    addon_utils.enable('nexo_uv', default_set=False)
    installed = sys.modules['nexo_uv']
    assert Path(installed.__file__).resolve().is_relative_to(scripts), installed.__file__
    assert hasattr(bpy.types.Scene, 'nexo_uv')
    assert 'FINISHED' in bpy.ops.nexo_uv.unwrap()
    layer = bpy.context.object.data.uv_layers.get('NexoUV')
    assert layer and len(layer.uv) > 0
    addon_utils.disable('nexo_uv', default_set=False)
    assert not hasattr(bpy.types.Scene, 'nexo_uv')
    print('NEXO_INSTALL_SMOKE_OK', installed.__file__)


if __name__ == '__main__':
    main()
