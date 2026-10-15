"""Package the Windows executable and README as a versioned release ZIP."""

import json
import re
import struct
import zipfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    executable = root / "build/executable/CognexDiscovery.exe"
    metadata = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*("[^"\n]+")\s*$', metadata, re.MULTILINE)
    if match is None:
        raise ValueError("Expected a static project version in pyproject.toml")
    version = json.loads(match[1])
    if not re.fullmatch(r"[0-9A-Za-z.+-]+", version):
        raise ValueError("Invalid release version")
    # Read the executable's architecture, independent of the packaging interpreter.
    with executable.open("rb") as binary:
        if binary.read(2) != b"MZ":
            raise ValueError("Expected a Windows executable")
        binary.seek(0x3C)
        pe_offset = struct.unpack("<I", binary.read(4))[0]
        binary.seek(pe_offset)
        if binary.read(4) != b"PE\0\0":
            raise ValueError("Invalid PE header")
        machine = struct.unpack("<H", binary.read(2))[0]
    architecture = {0x8664: "x64", 0xAA64: "arm64", 0x014C: "x86"}[machine]
    name = f"CognexDiscovery-v{version}-windows-{architecture}"
    destination = root / "dist"
    destination.mkdir(exist_ok=True)
    archive = destination / f"{name}.zip"
    temporary = archive.with_suffix(".zip.tmp")
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED
        ) as bundle:
            bundle.write(executable, f"{name}/CognexDiscovery.exe")
            bundle.write(root / "README.md", f"{name}/README.md")
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Release ZIP: {archive}")


if __name__ == "__main__":
    main()
