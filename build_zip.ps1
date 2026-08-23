# Package the model-panel plugin into an AstrBot-installable zip.
# Steps: bump version -> rebuild vite bundle -> pack zip.
# Only ASCII text on purpose: PS 5.1 on non-UTF8 codepages may mis-parse
# UTF-8 no-BOM files containing CJK comments, so keep this file pure ASCII.
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem

# resolve script dir (works when $PSScriptRoot is empty under some hosts)
if ($PSScriptRoot) {
    $_scriptDir = $PSScriptRoot
}
elseif ($MyInvocation.MyCommand.Path) {
    $_scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
else {
    $_scriptDir = (Get-Location).Path
}

$pluginName = "astrbot_plugin_model_panel"
$distDir = [System.IO.Path]::Combine($_scriptDir, "dist")
if (-not (Test-Path $distDir)) {
    New-Item -ItemType Directory -Path $distDir | Out-Null
}

# Step 1: bump version so the zip file name is unique.
# IMPORTANT: bump BEFORE building vite, because _bump_version.ps1 writes
# version.ts and the front-end bundle must embed the NEW version number.
$bumpScript = [System.IO.Path]::Combine($_scriptDir, "_bump_version.ps1")
function Invoke-BumpVersion {
    if (Test-Path $bumpScript) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $bumpScript -Root $_scriptDir
    }
}

$metaPath = [System.IO.Path]::Combine($_scriptDir, "metadata.yaml")
function Get-VersionFromMeta {
    param([string]$Path)
    $ver = "v0.1.0"
    if (Test-Path $Path) {
        try {
            $content = [System.IO.File]::ReadAllText($Path)
            $m = [regex]::Match($content, "(?m)^version:\s*(v?[0-9]+\.[0-9]+\.[0-9]+)")
            if ($m.Success) { $ver = $m.Groups[1].Value }
        }
        catch {
            # ignore
        }
    }
    return $ver
}

# If the declared metadata version has not been packed in dist yet, use it directly
# (first build for a new minor, e.g. after manually setting version to v0.6.0).
$declaredVersion = Get-VersionFromMeta -Path $metaPath
$declaredZip = [System.IO.Path]::Combine($distDir, "${pluginName}_${declaredVersion}.zip")
if (-not (Test-Path $declaredZip)) {
    Write-Host "build_zip: using declared version $declaredVersion (first build for it)"
    $version = $declaredVersion
} else {
    Invoke-BumpVersion
    $version = Get-VersionFromMeta -Path $metaPath
    # if the same-name zip already exists, bump again until we get a fresh one;
    # historical zips are always kept
    $guard = 0
    while (Test-Path ([System.IO.Path]::Combine($distDir, "${pluginName}_${version}.zip"))) {
        $guard++
        if ($guard -gt 50) {
            Write-Host "ERROR: same-name zip still exists after $guard bumps" -ForegroundColor Red
            exit 1
        }
        Invoke-BumpVersion
        $version = Get-VersionFromMeta -Path $metaPath
    }
}
$zipName = "${pluginName}_${version}.zip"
$zipPath = [System.IO.Path]::Combine($distDir, $zipName)
Write-Host "build_zip: target zip $zipName"

# Make sure version.ts matches the zip version (declaredVersion branch skips bump).
$versionFile = Join-Path (Join-Path $_scriptDir "webui-src") "src\version.ts"
$ts = "export const PLUGIN_VERSION = `"$version`";`n"
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
[System.IO.File]::WriteAllText($versionFile, $ts, $utf8Bom)
Write-Host "build_zip: version.ts -> $version"

# Step 2: rebuild vite bundle so bundle PLUGIN_VERSION matches the zip name
$webui = [System.IO.Path]::Combine($_scriptDir, "webui-src")
$packageJson = [System.IO.Path]::Combine($webui, "package.json")
$nodeModules = [System.IO.Path]::Combine($webui, "node_modules")
if (Test-Path $packageJson) {
    Push-Location $webui
    if (-not (Test-Path $nodeModules)) {
        Write-Host "build_zip: node_modules missing, running npm install..."
        npm install 2>&1 | Select-Object -Last 5 | ForEach-Object { Write-Host $_ }
    }
    Write-Host "build_zip: rebuilding vite bundle..."
    npm run build 2>&1 | Select-Object -Last 8 | ForEach-Object { Write-Host $_ }
    $npmExit = $LASTEXITCODE
    Pop-Location
    if ($npmExit -ne 0) {
        Write-Host "build_zip: ERROR vite build failed ($npmExit)" -ForegroundColor Red
        exit 1
    }
}
else {
    Write-Host "build_zip: WARNING webui-src/package.json missing, skip vite build"
}

# Step 3: pack zip (excluding build artifacts / agent scratch dirs)
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
$files = Get-ChildItem -Path $_scriptDir -Recurse -File -Force
$count = 0
foreach ($file in $files) {
    $rel = ($file.FullName.Substring($_scriptDir.Length + 1)) -replace "\\", "/"
    if (Should-Exclude $rel $file.Name) { continue }
    $entryName = "$pluginName/$rel"
    [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile($zip, $file.FullName, $entryName, "Optimal") | Out-Null
    $count++
}
$zip.Dispose()
Write-Host "build_zip: Packaged $zipPath files=$count"