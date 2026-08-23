# Auto bump plugin patch version before build.
# Version format: v0.{minor}.{patch}
#   - major = 0 (reserved)
#   - minor = manual, declared in metadata.yaml (e.g. "version: v0.2.0")
#             Bump only when shipping new features; reset patch to 0 when bumping minor.
#   - patch = auto-increment per build, sourced from the highest patch number
#             seen in dist/*.zip + metadata.yaml under the current minor.
#
# Behavior:
#   - Read metadata.yaml. If version matches v0.{minor}.{patch} with the same
#     minor as the highest seen patch, advance patch+1.
#   - If metadata version has a different minor (you bumped minor manually),
#     treat it as patch=0 and start from 1.
#   - Always write the resulting v0.{minor}.{patch} back to metadata.yaml
#     and to webui-src/src/version.ts.
[CmdletBinding()]
param(
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrEmpty($Root)) {
    if ($PSScriptRoot) {
        $Root = $PSScriptRoot
    } elseif ($MyInvocation.MyCommand.Path) {
        $Root = Split-Path -Parent $MyInvocation.MyCommand.Path
    } else {
        Write-Host "[bump_version] cannot resolve script root, skipping"
        return
    }
}

function Get-DistMaxPatch {
    # Scan dist/*.zip and return the highest patch number for the given minor.
    param([string]$DistDir, [string]$PluginName, [int]$Minor)
    if (-not (Test-Path $DistDir)) { return 0 }
    $files = Get-ChildItem -Path $DistDir -Filter "${PluginName}_v*.zip" -ErrorAction SilentlyContinue
    if (-not $files) { return 0 }
    $pattern = "^${PluginName}_v0\.${Minor}\.(\d+)\.zip$"
    $maxPatch = 0
    foreach ($f in $files) {
        $m = [regex]::Match($f.Name, $pattern)
        if ($m.Success) {
            $n = [int]$m.Groups[1].Value
            if ($n -gt $maxPatch) { $maxPatch = $n }
        }
    }
    return $maxPatch
}

function Get-MetadataVersion {
    # Parse metadata.yaml's version field. Returns @{ minor=N; patch=N } or $null.
    param([string]$MetaPath)
    if (-not (Test-Path $MetaPath)) { return $null }
    $txt = [System.IO.File]::ReadAllText($MetaPath)
    $m = [regex]::Match($txt, "(?m)^version:\s*v0\.(\d+)\.(\d+)")
    if ($m.Success) {
        return @{ minor = [int]$m.Groups[1].Value; patch = [int]$m.Groups[2].Value }
    }
    return $null
}

$metaPath = Join-Path $Root "metadata.yaml"
if (-not (Test-Path $metaPath)) {
    Write-Host "[bump_version] metadata.yaml missing, skipping"
    return
}

$distDir = Join-Path $Root "dist"
$pluginName = "astrbot_plugin_model_panel"

$metaVer = Get-MetadataVersion -MetaPath $metaPath
if ($null -eq $metaVer) {
    # No version in metadata; default to 0.0 and start patch=1.
    Write-Host "[bump_version] metadata.yaml has no version, starting from v0.0.1"
    $metaVer = @{ minor = 0; patch = 0 }
}

$minor = $metaVer.minor
$distPatch = Get-DistMaxPatch -DistDir $distDir -PluginName $pluginName -Minor $minor
$startPatch = [Math]::Max($metaVer.patch, $distPatch)
$nextPatch = $startPatch + 1
$version = "v0.${minor}.${nextPatch}"

# Write back to metadata.yaml (preserve other lines exactly).
$content = Get-Content -Path $metaPath -Raw -Encoding UTF8
$newContent = [regex]::Replace($content, "(?m)^version:\s*v?[0-9]+\.[0-9]+\.[0-9]+.*$", "version: $version")
if ($newContent -ne $content) {
    Set-Content -Path $metaPath -Value $newContent -Encoding UTF8 -NoNewline
    Write-Host "[bump_version] metadata.yaml version -> $version (minor=$minor, prevPatch=$startPatch)"
} else {
    Write-Host "[bump_version] metadata.yaml unchanged (already $version)"
}

# Write webui-src/src/version.ts.
$webui = Join-Path $Root "webui-src"
$versionFile = Join-Path $webui "src\version.ts"
$ts = "export const PLUGIN_VERSION = `"$version`";`n"
$utf8Bom = New-Object System.Text.UTF8Encoding($true)
[System.IO.File]::WriteAllText($versionFile, $ts, $utf8Bom)
Write-Host "[bump_version] version.ts -> $version"
