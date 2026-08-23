# Package the model-panel plugin into an AstrBot-installable zip.
# Output: dist/astrbot_plugin_model_panel_vX.Y.Z.zip with root dir astrbot_plugin_model_panel/
# 在打包前会自动调用 _bump_version.ps1 递增版本号。
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$root = $PSScriptRoot
$pluginName = "astrbot_plugin_model_panel"
$distDir = Join-Path $root "dist"
if (-not (Test-Path $distDir)) { New-Item -ItemType Directory -Path $distDir | Out-Null }

# 1. 先递增版本号
$bumpScript = Join-Path $root "_bump_version.ps1"
if (Test-Path $bumpScript) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $bumpScript -Root $root
}

# 2. 从最新 metadata.yaml 读 version
$metaPath = Join-Path $root "metadata.yaml"
$version = "v0.1.0"
if (Test-Path $metaPath) {
    $meta = [System.IO.File]::ReadAllText($metaPath)
    if ($meta -match 'version:\s*(v?[0-9]+\.[0-9]+\.[0-9]+)') {
        $version = $Matches[1]
    }
}

$zipName = "${pluginName}_${version}.zip"
$zipPath = Join-Path $distDir $zipName
if (Test-Path $zipPath) {
    Write-Host "Already packaged: $zipName. Delete it first to repack." -ForegroundColor Red
    exit 1
}

$excludeDirs  = @("dist", "webui-src", ".git", "__pycache__", ".codegraph", ".codebuddy", "node_modules", "tests", "docs", "_References")
$excludeExts  = @(".pyc", ".pyo")
$excludeFiles = @("build_zip.ps1", "build_webui.ps1", "_inspect_zip.ps1", ".gitignore", "_repack.ps1", "_bump_version.ps1")

function Should-Exclude($relPath, $fileName) {
    $parts = $relPath -split "/"
    foreach ($p in $parts) { if ($excludeDirs -contains $p) { return $true } }
    foreach ($e in $excludeExts) { if ($fileName.EndsWith($e)) { return $true } }
    foreach ($f in $excludeFiles) { if ($relPath -eq $f) { return $true } }
    return $false
}

$zip = [System.IO.Compression.ZipFile]::Open($zipPath, "Create")
$files = Get-ChildItem -Path $root -Recurse -File -Force
$count = 0
foreach ($file in $files) {
    $rel = ($file.FullName.Substring($root.Length + 1)) -replace "\\", "/"
    if (Should-Exclude $rel $file.Name) { continue }
    $entryName = "$pluginName/$rel"
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file.FullName, $entryName, "Optimal") | Out-Null
    $count++
}
$zip.dispose()
Write-Host "Packaged: $zipPath files=$count"
