# Build RiveShader.aex (Windows x64): the Rive Shader effect for After Effects. Windows counterpart of build.sh.
#   .\build.ps1                  configure + build + stage in %USERPROFILE%\AE-Dev-Plugins
#   .\build.ps1 clean            wipe the build folder first
#   .\build.ps1 install          also copy into After Effects' Plug-ins folder (asks for administrator rights)
#   .\build.ps1 dist             also make a zip
#   .\build.ps1 -GpuOnly         only the GPU module and rs_apply (no After Effects SDK needed)
# Needs: Visual Studio 2022 or its Build Tools with "Desktop development with C++" (MSVC, CMake and Ninja come with it).
# Deps (outside the repository), in %LOCALAPPDATA%\ae-plugin-deps:
#   AfterEffectsSDK   the Adobe After Effects SDK for Windows (your own download; or set AE_SDK)
#   wgpu-native       downloaded here from GitHub when missing, sha256 checked against wgpu-native.txt
# Works in Windows PowerShell 5.1 and PowerShell 7 (ASCII only: 5.1 reads a file without BOM in the ANSI code page).
param(
    [ValidateSet("build", "clean", "install", "dist")] [string] $Action = "build",
    [switch] $GpuOnly
)
$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$Deps = Join-Path $env:LOCALAPPDATA "ae-plugin-deps"
$AeSdk = if ($env:AE_SDK) { $env:AE_SDK } else { Join-Path $Deps "AfterEffectsSDK" }
$Wgpu = Join-Path $Deps "wgpu-native"
$Build = if ($GpuOnly) { Join-Path $Deps "build-RiveShader-gpu" } else { Join-Path $Deps "build-RiveShader" }
$Stage = Join-Path $env:USERPROFILE "AE-Dev-Plugins"
New-Item -ItemType Directory -Force -Path $Deps | Out-Null

function Check([string] $what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

# 1. the MSVC environment (cl, link, rc, cmake, ninja), unless this shell already is a developer prompt
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) { throw "Visual Studio not found: install Visual Studio 2022 (or its Build Tools) with 'Desktop development with C++'" }
    $vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $vs) { throw "Visual Studio has no C++ tools: add the 'Desktop development with C++' workload" }
    $vcvars = Join-Path $vs "VC\Auxiliary\Build\vcvars64.bat"
    foreach ($line in (cmd /c "`"$vcvars`" >nul && set")) {
        if ($line -match "^([^=]+)=(.*)$") { Set-Item -Path "env:$($Matches[1])" -Value $Matches[2] }
    }
    Write-Host "MSVC environment: $vs"
}

# 2. wgpu-native, pinned in wgpu-native.txt
$pin = @{}
foreach ($line in Get-Content (Join-Path $Here "wgpu-native.txt")) {
    if ($line -match "^([a-z0-9_-]+)=(.*)$") { $pin[$Matches[1]] = $Matches[2].Trim() }
}
if (-not (Test-Path (Join-Path $Wgpu "lib\wgpu_native.lib"))) {
    $asset, $sha = $pin["windows-x86_64"] -split "\s+"
    $url = "https://github.com/gfx-rs/wgpu-native/releases/download/$($pin['tag'])/$asset"
    $zip = Join-Path $Deps $asset
    Write-Host "wgpu-native $($pin['tag']): $url"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    $got = (Get-FileHash -Algorithm SHA256 $zip).Hash.ToLower()
    if ($got -ne $sha) { Remove-Item $zip; throw "wgpu-native: sha256 $got differs from wgpu-native.txt ($sha), archive deleted" }
    if (Test-Path $Wgpu) { Remove-Item -Recurse -Force $Wgpu }
    Expand-Archive -Path $zip -DestinationPath $Wgpu
    Remove-Item $zip
    Write-Host "wgpu-native: sha256 ok, unpacked in $Wgpu"
}

# 3. configure + build
if ($Action -eq "clean" -and (Test-Path $Build)) { Remove-Item -Recurse -Force $Build }
if (-not $GpuOnly -and -not (Test-Path (Join-Path $AeSdk "Examples\Headers\AE_Effect.h"))) {
    throw "After Effects SDK not found in $AeSdk (download it from developer.adobe.com and unzip it there, or set AE_SDK)"
}
$gpuFlag = if ($GpuOnly) { "ON" } else { "OFF" }
cmake -S $Here -B $Build -G Ninja -DCMAKE_BUILD_TYPE=Release "-DAE_SDK=$AeSdk" "-DWGPU_NATIVE=$Wgpu" "-DRS_GPU_ONLY=$gpuFlag"
Check "cmake configure"
if ($GpuOnly) {
    cmake --build $Build     # rsgpu, rshost (registry, file picker) and rs_apply
    Check "cmake build"
    Write-Host "built: $(Join-Path $Build 'rs_apply.exe')"
    exit 0
}
cmake --build $Build --target RiveShader
Check "cmake build"
$Aex = Join-Path $Build "RiveShader.aex"
Write-Host "built: $Aex"

# 4. stage
New-Item -ItemType Directory -Force -Path $Stage | Out-Null
Copy-Item -Force $Aex (Join-Path $Stage "RiveShader.aex")
Write-Host "staged: $(Join-Path $Stage 'RiveShader.aex')"

# 5. install: <After Effects>\Support Files\Plug-ins\Rive\RiveShader.aex (Program Files: administrator rights)
if ($Action -eq "install") {
    if (Get-Process -Name AfterFX -ErrorAction SilentlyContinue) { throw "install: quit After Effects first (the plugin is loaded)" }
    $ae = Get-ChildItem -Directory -Path (Join-Path $env:ProgramFiles "Adobe") -Filter "Adobe After Effects *" -ErrorAction SilentlyContinue |
        Sort-Object Name | Select-Object -Last 1
    if (-not $ae) { throw "After Effects not found in $(Join-Path $env:ProgramFiles 'Adobe')" }
    $dest = Join-Path $ae.FullName "Support Files\Plug-ins\Rive"
    $copy = "New-Item -ItemType Directory -Force -Path '$dest' | Out-Null; Copy-Item -Force '$Aex' '$dest\RiveShader.aex'"
    $admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($admin) { Invoke-Expression $copy }
    else { Start-Process -Verb RunAs -Wait -FilePath powershell.exe -ArgumentList "-NoProfile", "-Command", $copy }
    if (-not (Test-Path (Join-Path $dest "RiveShader.aex"))) { throw "install: copy to $dest failed" }
    Write-Host "installed: $dest\RiveShader.aex  (restart After Effects)"
}

# 6. optional distributable zip
if ($Action -eq "dist") {
    $h = Get-Content (Join-Path $Here "src\RiveShader.h") -Raw
    $v = @("MAJOR", "MINOR", "BUG") | ForEach-Object { if ($h -match "RS_$($_)_VERSION\s+(\d+)") { $Matches[1] } }
    $dist = Join-Path $Deps "dist"
    New-Item -ItemType Directory -Force -Path $dist | Out-Null
    $out = Join-Path $dist "RiveShader-win64-$($v -join '.').zip"
    Compress-Archive -Force -Path $Aex -DestinationPath $out
    Write-Host "dist: $out"
}
