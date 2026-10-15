[CmdletBinding()]
param([string]$Python = "")

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
if (-not $Python) {
    $Python = Join-Path $repoRoot ".venv/Scripts/python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python not found: $Python. Create .venv and install .[build] first (see README)."
}
$Python = (Resolve-Path -LiteralPath $Python).Path
& $Python -c "import PyInstaller, PySide6, psutil"
if ($LASTEXITCODE -ne 0) { throw "Install build dependencies with: $Python -m pip install -e .[build]" }
& $Python -m PyInstaller --noconfirm --clean `
    --distpath (Join-Path $repoRoot "build/executable") `
    --workpath (Join-Path $repoRoot "build/pyinstaller") `
    (Join-Path $PSScriptRoot "CognexDiscovery.spec")
if ($LASTEXITCODE -ne 0) { throw "Executable build failed." }
& $Python (Join-Path $PSScriptRoot "package_release.py")
if ($LASTEXITCODE -ne 0) { throw "Release ZIP packaging failed." }
