# Build single-file exe: powershell -ExecutionPolicy Bypass -File scripts\build.ps1
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = "C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if (-not (Test-Path $Python)) {
    $Python = "python"
}

$Dist = Join-Path $Root "dist"
$Exe = Join-Path $Dist "CivitaiPromptDL.exe"

# 发布策略：dist 只保留当前版本 exe；历史版本由 git tag 管理，不保留旧 exe 备份。
if (Test-Path $Exe) {
    try {
        $fs = [System.IO.File]::Open($Exe, 'Open', 'ReadWrite', 'None')
        $fs.Close()
    } catch {
        throw "CivitaiPromptDL.exe 正在运行，请先关闭程序再构建（dist 只保留当前版本，不做旧版备份）"
    }
}

Write-Host "== Running selftest =="
& $Python -m civitai_dl --selftest
if ($LASTEXITCODE -ne 0) { throw "Selftest failed, aborting build" }

Write-Host "== Running PyInstaller =="
& $Python -m PyInstaller --noconfirm --clean civitai_dl.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# 清理历史备份文件（如果手工留下过）
Get-ChildItem $Dist -Filter "CivitaiPromptDL_v*.exe" -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue

Copy-Item "docs\03-使用说明.md" (Join-Path $Dist "使用说明.md") -Force
Copy-Item "docs\04-误报处理说明.md" (Join-Path $Dist "误报处理说明.md") -Force
if (Test-Path "README.md") { Copy-Item "README.md" (Join-Path $Dist "README.md") -Force }

$SizeMb = [math]::Round((Get-Item $Exe).Length / 1MB, 1)
Write-Host "== Done: $Exe ($SizeMb MB) =="
