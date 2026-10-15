# Build with tools/release/build.ps1 on Windows.
from pathlib import Path

root = Path(SPECPATH).resolve().parents[1]
assets = root / "src/cognex_discovery/assets"
analysis = Analysis(
    [str(root / "tools/release/launcher.py")],
    pathex=[str(root / "src")],
    datas=[(str(assets / "cognex-discovery.ico"), "cognex_discovery/assets")],
    binaries=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="CognexDiscovery",
    console=False,
    icon=str(assets / "cognex-discovery.ico"),
    upx=False,
)
