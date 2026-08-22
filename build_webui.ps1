# Build Vue3 WebUI for the model-panel plugin.
# Reads version from metadata.yaml and injects it into the frontend,
# then runs vite build. Output goes to pages/model-panel/.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$webui = Join-Path $root "webui-src"

# Inject version from metadata.yaml
$metaPath = Join-Path $root "metadata.yaml"
$version = "v0.1.0"
if (Test-Path $metaPath) {
    $meta = [System.IO.File]::ReadAllText($metaPath)
    if ($meta -match 'version:\s*(v?[0-9]+\.[0-9]+\.[0-9]+)') {
        $version = $Matches[1]
    }
}
Write-Host "Using version $version"

# Write version.ts (gitignored, auto-generated)
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
npm run build 2>&1 | Select-Object -Last 12
if ($LASTEXITCODE -ne 0) {
    Write-Host "Build failed" -ForegroundColor Red
    exit 1
}
Pop-Location
Write-Host "WebUI built. Output: pages/model-panel/"
