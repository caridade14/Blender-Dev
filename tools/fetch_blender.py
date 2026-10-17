#!/usr/bin/env python3
"""Download a fixed official Blender release, verify SHA-256 and extract it."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile


VERSIONS = {"4.2.0", "4.3.2"}
PACKAGES = {
    ("linux", "x64"): "linux-x64.tar.xz",
    ("windows", "x64"): "windows-x64.zip",
    ("macos", "x64"): "macos-x64.dmg",
    ("macos", "arm64"): "macos-arm64.dmg",
}


def download(url, output):
    request = urllib.request.Request(url, headers={"User-Agent": "NexoUV-CI/0.1"})
    temporary = output.with_name(output.name + ".partial")
    try:
        # urllib's default HTTPS context keeps certificate and hostname checks.
        with urllib.request.urlopen(request, timeout=120) as response, temporary.open("wb") as target:
            if not response.geturl().startswith("https://download.blender.org/"):
                raise ValueError("Unexpected download redirect outside the official HTTPS host")
            shutil.copyfileobj(response, target, length=1024 * 1024)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def checksum_for(text, filename):
    for line in text.splitlines():
        match = re.fullmatch(r"([a-fA-F0-9]{64})\s+\*?(.+?)\s*", line)
        if match and match.group(2) == filename:
            return match.group(1).lower()
    raise ValueError(f"Official checksum list has no SHA-256 entry for {filename}")


def extract(archive, directory, system):
    if system == "linux":
        with tarfile.open(archive, "r:xz") as package:
            package.extractall(directory, filter="data")
        candidates = list(directory.glob("*/blender"))
    elif system == "windows":
        with zipfile.ZipFile(archive) as package:
            for member in package.infolist():
                target = (directory / member.filename).resolve()
                if not target.is_relative_to(directory.resolve()):
                    raise ValueError("ZIP member escapes the extraction directory")
            package.extractall(directory)
        candidates = list(directory.glob("*/blender.exe"))
    else:
        mount = Path(tempfile.mkdtemp(prefix="nexo-blender-mount-", dir=directory.parent))
        attached = False
        try:
            subprocess.run(["hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint", str(mount), str(archive)], check=True)
            attached = True
            apps = list(mount.glob("*.app"))
            if len(apps) != 1:
                raise ValueError("Expected exactly one Blender app in the official DMG")
            shutil.copytree(apps[0], directory / "Blender.app", symlinks=True)
        finally:
            if attached:
                subprocess.run(["hdiutil", "detach", str(mount)], check=True)
            mount.rmdir()
        candidates = [directory / "Blender.app" / "Contents" / "MacOS" / "Blender"]
    candidates = [path for path in candidates if path.is_file()]
    if len(candidates) != 1:
        raise ValueError(f"Expected one Blender executable, found {len(candidates)}")
    return candidates[0].resolve()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, choices=sorted(VERSIONS))
    parser.add_argument("--system", required=True, choices=["linux", "windows", "macos"])
    parser.add_argument("--arch", required=True, choices=["x64", "arm64"])
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args()
    host = {"Linux": "linux", "Windows": "windows", "Darwin": "macos"}.get(platform.system())
    arch = {"x86_64": "x64", "amd64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower())
    if (host, arch) != (args.system, args.arch):
        parser.error(f"Runner is {host}/{arch}, matrix requested {args.system}/{args.arch}")
    suffix = PACKAGES.get((args.system, args.arch))
    if suffix is None:
        parser.error("No supported official package for this platform")
    filename = f"blender-{args.version}-{suffix}"
    series = args.version.rsplit(".", 1)[0]
    base = f"https://download.blender.org/release/Blender{series}/"
    args.destination.mkdir(parents=True, exist_ok=True)
    checksum_path = args.destination / f"blender-{args.version}.sha256"
    download(base + checksum_path.name, checksum_path)
    expected = checksum_for(checksum_path.read_text(encoding="utf-8"), filename)
    archive = args.destination / filename
    if not archive.is_file() or sha256(archive) != expected:
        download(base + filename, archive)
    actual = sha256(archive)
    if actual != expected:
        archive.unlink()
        raise ValueError(f"SHA-256 mismatch for {filename}: expected {expected}, observed {actual}")
    directory = Path(tempfile.mkdtemp(prefix=f"blender-{args.version}-", dir=args.destination))
    executable = extract(archive, directory, args.system)
    print(json.dumps({"version": args.version, "system": args.system, "arch": args.arch,
                      "archive": filename, "sha256": actual, "executable": str(executable)}, indent=2))
    if args.github_output:
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"executable={executable}\n")


if __name__ == "__main__":
    main()
