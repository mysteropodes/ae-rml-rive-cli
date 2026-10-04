# AE RML via CLI Rive - installer (Windows). Windows counterpart of install.sh.
#
#   powershell -ExecutionPolicy Bypass -File install.ps1                      everything below
#   powershell -ExecutionPolicy Bypass -File install.ps1 rml2ae ae2rml        only what you name:
#                                                                             rml2ae ae2rml review-kit plugin skills
#
# The Rive CLI is never shipped here: it comes from Rive (releases.rive.app manifest, sha256 checked).
# Works in Windows PowerShell 5.1 and PowerShell 7. Asks before using administrator rights. ASCII only: Windows
# PowerShell 5.1 reads a file without BOM in the ANSI code page.
param([Parameter(ValueFromRemainingArguments = $true)] [string[]] $Parts)
# "Continue": native tools (python, pip, rive) may write to stderr; every step checks its own result
$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
if (-not $Parts -or $Parts.Count -eq 0) { $Parts = @("rml2ae", "ae2rml", "review-kit", "plugin", "skills") }
function Want([string] $p) { return $Parts -contains $p }
function Ok([string] $s) { Write-Host "  [ok] $s" -ForegroundColor Green }
function Warn([string] $s) { Write-Host "  [--] $s" -ForegroundColor Yellow }
function Fail([string] $s) { Write-Host "  [!!] $s" -ForegroundColor Red }
function Ask([string] $q) { $a = Read-Host "  $q [y/N]"; return ($a -eq "y" -or $a -eq "Y" -or $a -eq "o" -or $a -eq "O") }
function IsAdmin {
    return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}
# run a PowerShell command elevated (one UAC prompt) unless this shell already is
function AsAdmin([string] $command) {
    if (IsAdmin) { Invoke-Expression $command; return }
    Start-Process -Verb RunAs -Wait -FilePath powershell.exe -ArgumentList "-NoProfile", "-Command", $command
}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$AeDir = Get-ChildItem -Directory -Path (Join-Path $env:ProgramFiles "Adobe") -Filter "Adobe After Effects *" -ErrorAction SilentlyContinue |
    Sort-Object Name | Select-Object -Last 1

Write-Host "AE RML via CLI Rive - installing in $Root"

if ((Want "rml2ae") -or (Want "ae2rml") -or (Want "review-kit")) {
    Write-Host ""; Write-Host "1. Python"
    $base = $null
    foreach ($c in @(@("py", "-3"), @("python"), @("python3"))) {
        $exe = Get-Command $c[0] -ErrorAction SilentlyContinue
        if (-not $exe -or $exe.Source -like "*WindowsApps*") { continue }   # skip the Microsoft Store alias
        $args0 = @($c | Select-Object -Skip 1)
        & $c[0] @args0 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $base = $c; break }
    }
    if (-not $base) { Fail "Python 3.9+ not found: install it from python.org (tick 'Add python.exe to PATH') or 'winget install Python.Python.3.12'"; exit 1 }
    $args0 = @($base | Select-Object -Skip 1)
    Ok "$(& $base[0] @args0 --version)"
    if (-not (Test-Path $Py)) { & $base[0] @args0 -m venv $Venv; if ($LASTEXITCODE -ne 0) { Fail "could not create the venv"; exit 1 } }
    & $Py -m pip install -q --upgrade pip 2>$null | Out-Null
    # the package with its commands (ae, ae2rml, rml2ae in .venv\Scripts) and py-aep
    & $Py -m pip install -q -e ("{0}[ae2rml]" -f $Root)
    if ($LASTEXITCODE -eq 0) { Ok "Python package + dependencies (pillow, numpy, fontTools, py-aep) in $Venv" } else { Fail "pip failed (network?)" }
    & $Py -m pip install -q wgpu 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Ok "wgpu (optional: offline checks of the WGSL effect library)" } else { Warn "wgpu not installed (only for 'python -m rml2ae.ae2rml.fxlib check')" }

    Write-Host ""; Write-Host "2. Rive CLI"
    $riveHome = Join-Path $env:USERPROFILE ".rive\bin"
    $rive = Get-Command rive -ErrorAction SilentlyContinue
    if ($rive) { Ok "$(& rive --version 2>$null | Select-Object -First 1) ($($rive.Source))" }
    elseif (Test-Path (Join-Path $riveHome "rive.exe")) { Ok "rive in $riveHome (add it to PATH if 'rive' is not found)" }
    else {
        Warn "rive not found"
        try {
            $man = Invoke-RestMethod -Uri "https://releases.rive.app/cli/latest/manifest.json" -UseBasicParsing -ErrorAction Stop
            $key = $man.artifacts.PSObject.Properties.Name | Where-Object { $_ -match "^win" -and $_ -match "x64|x86_64|amd64" } | Select-Object -First 1
            if (-not $key) {
                Warn "Rive's manifest has no Windows build (platforms: $($man.artifacts.PSObject.Properties.Name -join ', ')): see rive.app for the CLI"
            } elseif (Ask "download the Rive CLI ($key) from releases.rive.app (sha256 verified) into $riveHome ?") {
                $art = $man.artifacts.$key
                $tmp = Join-Path $env:TEMP ("rive-" + [guid]::NewGuid())
                New-Item -ItemType Directory -Force -Path $tmp | Out-Null
                $file = Join-Path $tmp (Split-Path $art.path -Leaf)
                Invoke-WebRequest -Uri "https://releases.rive.app/cli/$($art.path)" -OutFile $file -UseBasicParsing -ErrorAction Stop
                $got = (Get-FileHash -Algorithm SHA256 $file).Hash.ToLower()
                if ($got -ne $art.sha256.ToLower()) { Fail "sha256 differs from the manifest, archive ignored" }
                else {
                    tar -xf $file -C $tmp
                    $exe = Get-ChildItem -Path $tmp -Recurse -Filter "rive.exe" | Select-Object -First 1
                    if (-not $exe) { Fail "no rive.exe in the archive" }
                    else {
                        New-Item -ItemType Directory -Force -Path $riveHome | Out-Null
                        Copy-Item -Force $exe.FullName (Join-Path $riveHome "rive.exe")
                        Ok "$(& (Join-Path $riveHome 'rive.exe') --version 2>$null | Select-Object -First 1) in $riveHome"
                        Warn "add $riveHome to your PATH (step 4 offers it)"
                    }
                }
                Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
            }
        } catch { Fail "Rive CLI download failed: $($_.Exception.Message)" }
    }

    Write-Host ""; Write-Host "3. ffmpeg (optional)"
    if (Get-Command ffmpeg -ErrorAction SilentlyContinue) { Ok "ffmpeg" } else { Warn "ffmpeg missing: 'ae render --out x.mp4' unavailable (winget install Gyan.FFmpeg)" }

    Write-Host ""; Write-Host "4. the ae, ae2rml and rml2ae commands"
    $scripts = Join-Path $Venv "Scripts"
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $userPath) { $userPath = "" }
    $missing = @($scripts, $riveHome) | Where-Object { ($userPath -split ";") -notcontains $_ }
    if (-not $missing) { Ok "$scripts is on your PATH" }
    elseif (Ask "add $($missing -join ' and ') to your user PATH?") {
        $newPath = (@($userPath.TrimEnd(";"), ($missing -join ";")) | Where-Object { $_ }) -join ";"
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Ok "PATH updated (open a new terminal)"
    } else { Warn "commands are in $scripts (ae.exe, ae2rml.exe, rml2ae.exe)" }

    Write-Host ""; Write-Host "5. the After Effects panel"
    if (-not $AeDir) { Warn "After Effects not found in $(Join-Path $env:ProgramFiles 'Adobe'): panel and plugin not installed" }
    else {
        Ok "After Effects: $($AeDir.FullName)"
        $panels = Join-Path $AeDir.FullName "Support Files\Scripts\ScriptUI Panels"
        $tmp = Join-Path $env:TEMP "Rive.jsx"
        $jsRoot = $Root.Replace("\", "\\")
        (Get-Content -Raw -Encoding UTF8 (Join-Path $Root "rml2ae\panel\Rive.jsx")) -replace '(?m)^  var ROOT = "[^"]*";', "  var ROOT = `"$jsRoot`";" |
            Set-Content -Encoding UTF8 -NoNewline $tmp
        if (Ask "copy the panel into `"$panels`" (administrator rights)?") {
            AsAdmin "Copy-Item -Force '$tmp' '$panels\Rive.jsx'"
            if (Test-Path (Join-Path $panels "Rive.jsx")) { Ok "panel copied: Window > Rive.jsx (restart After Effects)" } else { Fail "panel not copied" }
        } else { Warn "do it by hand: copy $tmp into `"$panels`"" }
        Write-Host "     In After Effects: Edit > Preferences > Scripting & Expressions > tick `"Allow Scripts to Write Files and Access Network`""
    }
}

if (Want "ae2rml") {
    Write-Host ""; Write-Host "== ae2rml (.aep -> Rive CLI project): reads .aep files with py-aep, After Effects not launched"
    Ok "installed with the package above"
    Write-Host "     try:  .venv\Scripts\ae2rml examples\demo.aep out\demo --verify"
}
if (Want "review-kit") {
    Write-Host ""; Write-Host "== review kit (Frame.io-like notes inside the Rive CLI viewer)"
    Ok "nothing to install: .venv\Scripts\python review-kit\review_install.py <your Rive CLI project>   (see review-kit\README.md)"
}
if (Want "plugin") {
    Write-Host ""; Write-Host "== Rive Shader plugin for After Effects (runs a Rive .wgsl as an effect) - built from source"
    $sdk = if ($env:AE_SDK) { $env:AE_SDK } else { Join-Path $env:LOCALAPPDATA "ae-plugin-deps\AfterEffectsSDK" }
    if (Test-Path (Join-Path $sdk "Examples\Headers\AE_Effect.h")) {
        & (Join-Path $Root "rml2ae\plugin\build.ps1") install
        Ok "plugin built and installed (quit After Effects first)"
    } else {
        Warn "needs the Adobe After Effects SDK for Windows (free, developer.adobe.com) unzipped in $sdk"
        Warn "and Visual Studio 2022 (or its Build Tools) with 'Desktop development with C++'"
        Warn "then:  powershell -ExecutionPolicy Bypass -File rml2ae\plugin\build.ps1 install"
    }
}
if (Want "skills") {
    Write-Host ""; Write-Host "== agent skills (Claude Code): how to drive these tools"
    $claude = Join-Path $env:USERPROFILE ".claude"
    if (Test-Path $claude) {
        New-Item -ItemType Directory -Force -Path (Join-Path $claude "skills") | Out-Null
        Copy-Item -Recurse -Force (Join-Path $Root "skills\*") (Join-Path $claude "skills")
        Ok "skills copied to $claude\skills"
    } else { Warn "no $claude : copy skills\* into your agent's skill folder" }
}
if ((Test-Path $Py) -and ((Want "rml2ae") -or (Want "plugin"))) {
    Write-Host ""; Write-Host "verification"
    & $Py -m rml2ae.ae doctor
}
Write-Host ""; Write-Host "Done."
