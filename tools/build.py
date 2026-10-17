#!/usr/bin/env python3
"""Validate and reproducibly package the Blender add-on using Python 3.11+."""

from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path
import re
import sys
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "nexo_uv"
STAMP = (2024, 1, 1, 0, 0, 0)
NATIVE_SUFFIXES = {".exe", ".dll", ".so", ".dylib", ".pyd", ".a", ".lib", ".whl"}
BLENDER_MODULES = {"bpy", "bmesh", "mathutils", "bpy_extras", "blf", "gpu", "gpu_extras", "numpy", "nexo_uv"}


def addon_files():
    for path in sorted(ADDON.rglob("*")):
        relative = path.relative_to(ADDON)
        if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
            continue
        if path.suffix.lower() in {".pyc", ".pyo"}:
            continue
        if path.is_symlink():
            raise ValueError(f"Symlinks are not accepted in an add-on package: {relative}")
        if path.is_file():
            yield relative.as_posix(), path.read_bytes()


def validate(files):
    manifest = tomllib.loads(dict(files)["blender_manifest.toml"].decode("utf-8"))
    if manifest.get("id") != "nexo_uv" or manifest.get("type") != "add-on":
        raise ValueError("The manifest must identify the nexo_uv add-on")
    version = manifest.get("version", "")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Expected a three-part numeric release version")
    minimum = tuple(int(v) for v in manifest["blender_version_min"].split("."))
    if minimum < (4, 2, 0):
        raise ValueError("The minimum supported Blender is 4.2.0")
    if "SPDX:GPL-3.0-or-later" not in manifest.get("license", []):
        raise ValueError("The manifest must retain the declared GPL license")
    contents = dict(files)
    license_text = contents.get("LICENSE.txt", b"")
    if b"GNU GENERAL PUBLIC LICENSE" not in license_text or b"Version 3" not in license_text:
        raise ValueError("nexo_uv/LICENSE.txt must contain the full GPL version 3 license")
    if not contents.get("assets/logo.png", b"").startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("The add-on logo must be present as assets/logo.png")
    addon_info = None
    for name, data in files:
        path = Path(name)
        if "engine" in path.parts or path.suffix.lower() in NATIVE_SUFFIXES:
            raise ValueError(f"Native engine/dependency is prohibited: {name}")
        if data.startswith((b"MZ", b"\x7fELF", bytes.fromhex("cafebabe"), bytes.fromhex("cffaedfe"))):
            raise ValueError(f"Native executable payload is prohibited: {name}")
        if path.suffix != ".py":
            continue
        compile(data, name, "exec")
        tree = ast.parse(data, filename=name)
        if name == "__init__.py":
            for node in tree.body:
                if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "bl_info" for t in node.targets):
                    addon_info = ast.literal_eval(node.value)
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                modules = [(node.module or "").split(".")[0]]
            for module in modules:
                if module in {"ctypes", "subprocess"}:
                    raise ValueError(f"External native-engine loading is prohibited: {name}: {module}")
                if module not in sys.stdlib_module_names | BLENDER_MODULES:
                    raise ValueError(f"Unbundled dependency: {name}: {module}")
    if addon_info is None:
        raise ValueError("Missing literal bl_info metadata")
    if tuple(addon_info["version"]) != tuple(int(v) for v in version.split(".")):
        raise ValueError("bl_info and manifest versions disagree")
    if tuple(addon_info["blender"]) != minimum:
        raise ValueError("bl_info and manifest Blender minimums disagree")
    return version


def archive(path, files, prefix):
    # Stored entries make byte output independent of the host zlib version.
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as package:
        for name, data in files:
            info = zipfile.ZipInfo(prefix + name, STAMP)
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            package.writestr(info, data)
    with zipfile.ZipFile(path) as package:
        if package.testzip() is not None:
            raise ValueError(f"Archive CRC check failed: {path}")
        for name in package.namelist():
            if name.endswith(".py"):
                compile(package.read(name), name, "exec")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    parser.add_argument("--check", action="store_true", help="Validate source/package prerequisites without writing archives")
    args = parser.parse_args()
    files = list(addon_files())
    version = validate(files)
    if args.check:
        print(f"Nexo UV {version}: {len(files)} files validated")
        return
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = [
        (args.output_dir / f"nexo_uv-{version}-legacy.zip", "nexo_uv/"),
        (args.output_dir / f"nexo_uv-{version}.zip", ""),
    ]
    sums = []
    for path, prefix in outputs:
        archive(path, files, prefix)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        sums.append(f"{digest}  {path.name}\n")
        print(f"{path}: {digest}")
    (args.output_dir / "SHA256SUMS").write_text("".join(sums), encoding="ascii", newline="\n")


if __name__ == "__main__":
    main()
