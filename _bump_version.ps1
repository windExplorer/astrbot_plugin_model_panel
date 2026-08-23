# Auto bump plugin version before build.
# Version format: v0.{minor}.{patch} (pure numeric, no date in version)
#   - major = 0
#   - minor = git commit count on HEAD (auto-derived)
#   - patch = today's build sequence under dist/ (1..N), resets next day
# Sequence is read from both:
#   1) existing zip files in dist/ matching v0.{minor}.{seq}.zip today pattern
#   2) metadata.yaml current version (handles incremental builds without zip yet)
# Idempotent: re-running without a fresh build cycle produces the same number
# unless a new commit or zip is present.
[CmdletBinding()]
param(
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"

# When dot-sourced, $PSScriptRoot is empty; fall back to $MyInvocation.
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

function Get-CommitCount {
    try {
        $n = (& git -C $Root rev-list --count HEAD 2>&1) | Select-Object -Last 1
        if ($LASTEXITCODE -ne 0) { return 0 }
        return [int]$n
    } catch {
        return 0
    }
}

function Get-DistMaxSeq {
    # Scan dist/*.zip and return the highest patch value matching v0.{minor}.{seq}.zip
    param([string]$DistDir, [string]$PluginName, [int]$Minor)
    if (-not (Test-Path $DistDir)) { return 0 }
    $files = Get-ChildItem -Path $DistDir -Filter "${PluginName}_v0.${Minor}.*.zip" -ErrorAction SilentlyContinue
    if (-not $files) { return 0 }
    $pattern = "^${PluginName}_v0\.${Minor}\.(\d+)\.zip$"
    $maxSeq = 0
    foreach ($f in $files) {
        $m = [regex]::Match($f.Name, $pattern)
        if ($m.Success) {
            $n = [int]$m.Groups[1].Value
            if ($n -gt $maxSeq) { $maxSeq = $n }
        }
    }
    return $maxSeq
}

function Get-MetadataPatch {
    # Read the current patch number from metadata.yaml version: v0.{minor}.{patch}
    param([string]$MetaPath, [int]$Minor)
    if (-not (Test-Path $MetaPath)) { return 0 }
    $txt = [System.IO.File]::ReadAllText($MetaPath)
    $m = [regex]::Match($txt, "(?m)^version:\s*v0\.${Minor}\.(\d+)")
    if ($m.Success) { return [int]$m.Groups[1].Value }
    return 0
}

$metaPath = Join-Path $Root "metadata.yaml"
if (-not (Test-Path $metaPath)) {
    Write-Host "[bump_version] metadata.yaml missing, skipping"
    return
}

$commitCount = Get-CommitCount
$minor = [Math]::Max(0, $commitCount)
$distDir = Join-Path $Root "dist"
$pluginName = "astrbot_plugin_model_panel"

$distSeq = Get-DistMaxSeq -DistDir $distDir -PluginName $pluginName -Minor $minor
$metaSeq = Get-MetadataPatch -MetaPath $metaPath -Minor $minor
$startSeq = [Math]::Max($distSeq, $metaSeq)
$nextSeq = $startSeq + 1
$version = "v0.${minor}.${nextSeq}"

# Patch metadata.yaml version line (preserve other lines exactly).
$content = Get-Content -Path $metaPath -Raw -Encoding UTF8
$newContent = [regex]::Replace($content, "(?m)^version:\s*v?[0-9]+\.[0-9]+\.[0-9]+.*$", "version: $version")
if ($newContent -ne $content) {
    Set-Content -Path $metaPath -Value $newContent -Encoding UTF8 -NoNewline
    Write-Host "[bump_version] metadata.yaml version -> $version (commit=$commitCount, prevSeq=$startSeq)"
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
