# Build Vue3 WebUI for the model-panel plugin.
# 默认不 bump 版本号（让 build_zip.ps1 独占 bump，避免重复）。
# 如需单独 bump，可显式指定 -BumpVersion。
# 读取 metadata.yaml 当前版本注入前端，再运行 vite build。
# 输出到 pages/model-panel/。
[CmdletBinding()]
param(
    [switch]$BumpVersion
)

$ErrorActionPreference = "Stop"
# $PSScriptRoot can be empty under some hosts; fall back gracefully.
if ($PSScriptRoot) {
    $root = $PSScriptRoot
} elseif ($MyInvocation.MyCommand.Path) {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Path
} else {
    $root = (Get-Location).Path
}
$webui = Join-Path $root "webui-src"

if ($BumpVersion) {
    $bumpScript = Join-Path $root "_bump_version.ps1"
    if (Test-Path $bumpScript) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $bumpScript -Root $root
    }
}

# 读版本号
$metaPath = Join-Path $root "metadata.yaml"
$version = "v0.1.0"
if (Test-Path $metaPath) {
    $meta = [System.IO.File]::ReadAllText($metaPath)
    if ($meta -match 'version:\s*(v?[0-9]+\.[0-9]+\.[0-9]+)') {
        $version = $Matches[1]
    }
}
Write-Host "Using version $version"

# 写 version.ts
$versionFile = Join-Path $webui "src\version.ts"
$versionTs = "export const PLUGIN_VERSION = `"$version`";`n"
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
[System.IO.File]::WriteAllText($versionFile, $versionTs, $utf8Bom)

if (-not (Test-Path (Join-Path $webui "node_modules"))) {
    Write-Host "Installing dependencies..."
    Push-Location $webui
    npm install 2>&1 | Select-Object -Last 5
    Pop-Location
}

Push-Location $webui
npm run build 2>&1 | Select-Object -Last 20
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed" -ForegroundColor Red
    exit 1
}
Pop-Location
Write-Host "WebUI built. Output: pages/model-panel/"
