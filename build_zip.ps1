$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem

# 直接用 [System.IO.Path]::PathSeparator 与 .Path.Combine 代替字符串拼接，
# 避免 PowerShell 5 中 "\metadata.yaml" 被识别为转义序列或拼接被截断的诡异问题。

# 解析脚本所在目录（用 PSScriptRoot，缺省回退 MyInvocation）
$_scriptDir = $PSScriptRoot
if (-not $_scriptDir -and $MyInvocation.MyCommand.Path) {
    $_scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if (-not $_scriptDir) {
    $_scriptDir = (Get-Location).Path
}

$pluginName = "astrbot_plugin_model_panel"
$distDir = [System.IO.Path]::Combine($_scriptDir, "dist")
if (-not (Test-Path $distDir)) {
    New-Item -ItemType Directory -Path $distDir | Out-Null
}

# 1. bump version 函数
$bumpScript = [System.IO.Path]::Combine($_scriptDir, "_bump_version.ps1")
function Invoke-BumpVersion {
    if (Test-Path $bumpScript) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $bumpScript -Root $_scriptDir
    }
}

# 2. 把读 version 的逻辑 inline（不嵌进 function 定义，避免 PowerShell 解析变量时丢上下文）
function Get-VersionFromMeta {
    param([string]$Path)
    $ver = "v0.1.0"
    if (Test-Path $Path) {
        try {
            $content = [System.IO.File]::ReadAllText($Path)
            $m = [regex]::Match($content, "(?m)^version:\s*(v?[0-9]+\.[0-9]+\.[0-9]+)")
            if ($m.Success) { $ver = $m.Groups[1].Value }
        } catch {
            # ignore
        }
    }
    return $ver
}

# 先 bump 一次拿到 version
Invoke-BumpVersion
$metaPath = [System.IO.Path]::Combine($_scriptDir, "metadata.yaml")
Write-Host ("[build_zip] scriptDir=[" + $_scriptDir + "]")
Write-Host ("[build_zip] metaPath=[" + $metaPath + "] exists=" + (Test-Path $metaPath))

$version = Get-VersionFromMeta -Path $metaPath
$zipName = "${pluginName}_${version}.zip"
$zipPath = [System.IO.Path]::Combine($distDir, $zipName)

# 同名 zip 已存在 → 再次 bump 拿新 version。历史 zip 全部保留不删除。
$guard = 0
while (Test-Path $zipPath) {
    $guard++
    if ($guard -gt 50) {
        Write-Host "ERROR: 同名 zip 连续 $guard 次仍存在，bump 失败" -ForegroundColor Red
        exit 1
    }
    Invoke-BumpVersion
    $version = Get-VersionFromMeta -Path $metaPath
    $zipName = "${pluginName}_${version}.zip"
    $zipPath = [System.IO.Path]::Combine($distDir, $zipName)
}
Write-Host ("[build_zip] target zip: " + $zipName)

# 3. 打包
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
Write-Host ("[build_zip] Packaged: " + $zipPath + " files=" + $count)
