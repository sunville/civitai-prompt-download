# Build single-file exe: powershell -ExecutionPolicy Bypass -File scripts\build.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = "C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

Write-Host "== Running selftest =="
& $Python -m civitai_dl --selftest
if ($LASTEXITCODE -ne 0) { throw "Selftest failed, aborting build" }

Write-Host "== Running PyInstaller =="
& $Python -m PyInstaller --noconfirm --clean civitai_dl.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$Dist = Join-Path $Root "dist"
Copy-Item "docs\03-使用说明.md" (Join-Path $Dist "使用说明.md") -Force
Copy-Item "docs\04-误报处理说明.md" (Join-Path $Dist "误报处理说明.md") -Force
if (Test-Path "README.md") { Copy-Item "README.md" (Join-Path $Dist "README.md") -Force }

$Exe = Join-Path $Dist "CivitaiPromptDL.exe"
$SizeMb = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
Write-Host "== Done: $Exe ($SizeMb MB) =="
