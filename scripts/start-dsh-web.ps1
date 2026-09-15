# Start DeepSeek Harness Web UI with the EasyCAD plugin (official host + client).
# Usage:  powershell -File scripts\start-dsh-web.ps1

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$dshRoot = Join-Path $repoRoot "deepseek-harness"
$wingetNode = "C:\Users\helloworld\AppData\Local\Microsoft\WinGet\Packages\OpenJS.NodeJS.22_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v22.23.2-win-x64"
$patchPath = Join-Path $repoRoot "easycad\.generated-cordis.patch.yml"
$skillDir = (Join-Path $repoRoot ".dsh\skills") -replace "\\", "/"
$pluginDir = (Resolve-Path (Join-Path $repoRoot "easycad\dsh-plugin")).Path

function Find-NodeHome {
    $cmd = Get-Command node -ErrorAction SilentlyContinue
    if ($cmd) {
        return [System.IO.Path]::GetDirectoryName($cmd.Source)
    }
    if (Test-Path (Join-Path $wingetNode "node.exe")) {
        return $wingetNode
    }
    throw "Node.js 22 not found. Install OpenJS.NodeJS.22 via winget or add node to PATH."
}

function Find-EasyCadPython {
    if ($env:EASYCAD_PYTHON -and (Test-Path $env:EASYCAD_PYTHON)) {
        return $env:EASYCAD_PYTHON
    }
    $candidates = @(
        "D:\LeStoreDownload\anaconda3\envs\multi_agent_cad\python.exe",
        (Join-Path $env:USERPROFILE "anaconda3\envs\multi_agent_cad\python.exe"),
        (Join-Path $env:USERPROFILE "miniconda3\envs\multi_agent_cad\python.exe")
    )
    foreach ($path in $candidates) {
        if ($path -and (Test-Path $path)) { return $path }
    }
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return $py.Source }
    throw "EasyCAD Python not found. Set EASYCAD_PYTHON to the multi_agent_cad interpreter."
}

function Ensure-Junction([string]$link, [string]$target) {
    $parent = Split-Path $link
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    if (Test-Path $link) {
        $item = Get-Item $link -Force
        if ($item.Attributes.ToString() -match "ReparsePoint") { return }
        throw "Refusing to replace existing path that is not a junction: $link"
    }
    cmd /c mklink /J `"$link`" `"$target`" | Out-Null
}

if (-not (Test-Path $dshRoot)) {
    throw "deepseek-harness not found at $dshRoot"
}

$nodeHome = Find-NodeHome
$python = Find-EasyCadPython
$env:EASYCAD_PYTHON = $python
$env:PYTHONUTF8 = "1"
$env:Path = "$nodeHome;" + $env:Path

# ESM import('easycad') is resolved from $DSH_HOME/profiles/web, not NODE_PATH.
# dsh already walks $DSH_HOME/profiles/node_modules (healProfilesModuleFallback).
$dshHome = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $env:USERPROFILE ".dsh" }
Ensure-Junction (Join-Path $dshHome "profiles\node_modules\easycad") $pluginDir
Ensure-Junction (Join-Path $dshRoot "node_modules\easycad") $pluginDir
$shim = Join-Path $repoRoot "easycad\.dsh-modules"
Ensure-Junction (Join-Path $shim "easycad") $pluginDir
$env:NODE_PATH = $shim

$patch = @"
- id: skill-filesystem
  name: '@deepseek-ai/dsh-skill-filesystem'
  config:
    customSkillDirs:
      - '$skillDir'
- insert:
    - id: easycad
      name: easycad
"@
$utf8 = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($patchPath, $patch.Replace("`r`n", "`n") + "`n", $utf8)

$busy = Get-NetTCPConnection -LocalPort 3080 -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-Host "Port 3080 is already in use. Open http://127.0.0.1:3080"
    Write-Host "Stop the old dsh process first if you need this new CAD patch to load."
    exit 0
}

Set-Location $dshRoot

Write-Host "DeepSeek Harness Web UI + EasyCAD plugin"
Write-Host "  workspace hint: $repoRoot"
Write-Host "  python:         $python"
Write-Host "  patch:          $patchPath"
Write-Host "  URL:            http://127.0.0.1:3080"
Write-Host "Set the DeepSeek API key in Settings -> Model, workspace = EasyCAD root."
Write-Host "CAD pane uses official dsh slots (same window)."
Write-Host ""

pnpm --dir $dshRoot dsh --profile web --patch $patchPath --no-open --host 127.0.0.1 --port 3080
