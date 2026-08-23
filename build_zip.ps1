$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem
if ($PSScriptRoot) {
    $root = $PSScriptRoot
} elseif ($MyInvocation.MyCommand.Path) {
    $root = Split-Path -Parent $MyInvocation.MyCommand.Path
} else {
    $root = (Get-Location).Path
}
$pluginName = "astrbot_plugin_model_panel"
$distDir = Join-Path $root "dist"
if (-not (Test-Path $distDir)) { New-Item -ItemType Directory -Path $distDir | Out-Null }

# 1. bump version
$bumpScript = Join-Path $root "_bump_version.ps1"
if (Test-Path $bumpScript) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File $bumpScript -Root $root
}

# 2. read version
$metaPath = Join-Path $root "metadata.yaml"
$version = "v0.1.0"
if (Test-Path $metaPath) {
    $meta = [System.IO.File]::ReadAllText($metaPath)
    if ($meta -match "version:\s*(v?[0-9]+\.[0-9]+\.[0-9]+)") {
        $version = $Matches[1]
    }
}

$zipName = "${pluginName}_${version}.zip"
$zipPath = Join-Path $distDir $zipName
if (Test-Path $zipPath) {
    Write-Host "Already packaged: $zipName. Delete it first to repack."
    exit 1
}

$excludeDirs  = @("dist", "webui-src", ".git", "__pycache__", ".codegraph", ".codebuddy", "node_modules", "tests", "docs", "_References", ".reasonix")
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
$zip.Dispose()
Write-Host "Packaged: $zipPath files=$count"