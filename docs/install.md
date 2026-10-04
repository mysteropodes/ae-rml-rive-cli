# Installation

macOS first; the [Windows](#windows) section says what differs.

The repository installs as a whole or part by part. After Effects 2024 or later is needed for rml2ae and the plugin (developed and measured on After Effects 2026); `ae2rml` and the review kit do not need After Effects.

## One command

From the repository root:

```bash
./install.sh                                   # everything
./install.sh rml2ae ae2rml review-kit plugin skills    # or only the parts you name
```

| Part | What it does |
|---|---|
| `rml2ae` | Core: Python environment, Rive CLI check, the `ae` command, the After Effects panel (runs `rml2ae/install.sh`). Also run for `ae2rml` and `review-kit`, which share the environment. |
| `ae2rml` | Installs `py-aep` (reads `.aep` files without After Effects) and, optionally, `wgpu` for offline checks of the WGSL effect library. |
| `review-kit` | Nothing to install; usage is `python3 review-kit/review_install.py <project>`. |
| `plugin` | Builds and installs the Rive Shader plugin from source (see below). |
| `skills` | Copies `skills/*` into `~/.claude/skills` for Claude Code. |

The installer says what it finds at each step and asks before using administrator rights.

## What the core step does

1. **Python 3.9+** and a virtual environment at the repository root (`.venv`) with Pillow, NumPy, fontTools and py-aep. If Python is missing: `xcode-select --install` or `brew install python`.
2. **Rive CLI.** It is never shipped in this repository. If `rive` is not found, the installer offers either `brew install --cask rive-app/tap/rive-cli` (Homebrew tap `rive-app/tap`) or a direct download from `releases.rive.app`: the public manifest lists the version and sha256 per platform, the archive is checked against the sha256, and `rive` is installed in `~/.rive/bin` (add it to your PATH if needed). Check with `rive --version`.
3. **ffmpeg** (optional), for `ae render --out file.mp4`: `brew install ffmpeg`.
4. **The `ae` command**: a symlink at `/usr/local/bin/ae` (administrator password if needed), or a line to add to your shell: `export PATH="<repository>/rml2ae/bin:$PATH"`.
5. **The After Effects panel**: `rml2ae/panel/Rive.jsx` is copied to `Scripts/ScriptUI Panels` of your After Effects, with the repository path written into it. Relaunch After Effects, then open **Window › Rive.jsx**.
6. **The plugin**, if a built copy is found (see below).
7. **`ae doctor`** at the end.

Manual panel install: `sudo cp rml2ae/panel/Rive.jsx "/Applications/Adobe After Effects 2026/Scripts/ScriptUI Panels/"`, then edit the `ROOT` path at the top of the file if the repository is elsewhere.

## After Effects preference

In After Effects: **Preferences › Scripts & Expressions › Allow Scripts to Write Files and Access Network**. Tick it, then relaunch After Effects. Builds, pull and the panel need it.

## Fonts

Install the fonts of your projects in macOS. After Effects looks them up by PostScript name. `ae doctor <project>` lists the font files a project uses.

## The Rive Shader plugin

Optional. It lets After Effects run a Rive `.wgsl` shader as is. It is built from source because the Adobe After Effects SDK cannot be redistributed:

1. Download the free After Effects SDK from developer.adobe.com and unzip it into `~/.cache/ae-plugin-deps/AfterEffectsSDK` (or set `AE_SDK`). Build dependencies such as wgpu-native also live under `~/.cache/ae-plugin-deps`.
2. `./install.sh plugin` (or `cd rml2ae/plugin && ./build.sh install`). Quit After Effects first.
3. The plugin is not signed with an Apple Developer ID. The first time macOS may refuse to load it: allow it once in **System Settings › Privacy & Security**, then relaunch After Effects.

Verify with `ae doctor` (line "Rive Shader plugin") and, in After Effects, the effect **Rive › Rive Shader**. Without the plugin, rml2ae falls back to native After Effects effects. See [rive-shader-plugin.md](rive-shader-plugin.md).

## Checking the install

```bash
ae doctor                      # environment
ae doctor my_project           # plus the project: scene.rml, fonts, artboards
.venv/bin/python -m rml2ae.ae2rml examples/demo.aep out/demo --verify    # ae2rml, After Effects not launched
```

Every line should read `[ok]`. Typical fixes: a missing `rive` (see step 2), a missing `ffmpeg`, After Effects not running (only needed for build, watch, pull, diff).

## Running without the installer

The repository is a Python package (`pyproject.toml`), so `pip` can install it with its commands:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[ae2rml]"
.venv/bin/ae2rml examples/demo.aep out/demo      # .aep -> Rive CLI project (same as python -m rml2ae.ae2rml)
.venv/bin/rml2ae my_project                      # converter only: writes the .jsx and the report
.venv/bin/ae doctor                              # the `ae` command (same as rml2ae/bin/ae)
```

Extras: `ae2rml` (py-aep, to read `.aep` files), `wgpu` (offline checks of the WGSL effect library), `dev` (py-aep and
ruff, what the CI runs). `-e` keeps the install pointing at the repository, so edits and `git pull` take effect at once;
the installer (`./install.sh`) is unchanged and does not need this.

The `ae` wrapper (`rml2ae/bin/ae`) uses `.venv/bin/python` at the repository root if present, otherwise `python3`.

## Day-to-day constraints

- Save the After Effects project before `ae build --replace` or `ae watch` (they build in the open project).
- Do not leave a dialog open in After Effects during a build: scripts would be blocked.
- Optional per-project side files (`ae_passes.json`, `ae_audio.json`) are described in [rml2ae.md](rml2ae.md).

## Windows

The same parts install with `install.ps1`, from PowerShell at the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1                          # everything
powershell -ExecutionPolicy Bypass -File install.ps1 rml2ae ae2rml review-kit plugin skills
```

| Step | Windows |
|---|---|
| Python | Python 3.9+ from python.org (tick "Add python.exe to PATH") or `winget install Python.Python.3.12`. The installer creates `.venv` and runs `pip install -e ".[ae2rml]"`: the `ae`, `ae2rml` and `rml2ae` commands land in `.venv\Scripts`, and it offers to add that folder to your user PATH. |
| Rive CLI | Read from the same `releases.rive.app` manifest, sha256 checked, into `%USERPROFILE%\.rive\bin`. If the manifest has no Windows build, the installer says so and lists the platforms it has. |
| ffmpeg | Optional: `winget install Gyan.FFmpeg`. |
| Panel | `Rive.jsx` with the repository path written in, copied to `<After Effects>\Support Files\Scripts\ScriptUI Panels` (one administrator prompt). |
| Plugin | `rml2ae\plugin\build.ps1 install` when the After Effects SDK is found (see below). |
| Skills | Copied into `%USERPROFILE%\.claude\skills`. |

After Effects is looked for in `C:\Program Files\Adobe\Adobe After Effects <version>`. `ae build`, `ae pull` and the
panel send their scripts with `AfterFX.exe -r <script.jsx>`, which hands the script to the running After Effects;
`ae render` uses `aerender.exe`. Fonts are read from `C:\Windows\Fonts`, the per-user fonts folder and Adobe Fonts.
The plugin's shader registry is `%APPDATA%\RiveShader\shaders.tsv`.

### The Rive Shader plugin on Windows

1. Visual Studio 2022, or its free Build Tools, with the **Desktop development with C++** workload (it brings MSVC,
   CMake and Ninja).
2. The After Effects SDK **for Windows**, unzipped into `%LOCALAPPDATA%\ae-plugin-deps\AfterEffectsSDK` (or set
   `AE_SDK`).
3. `powershell -ExecutionPolicy Bypass -File rml2ae\plugin\build.ps1 install`, After Effects closed. The script
   downloads wgpu-native (the release pinned in `rml2ae/plugin/wgpu-native.txt`, sha256 checked), builds
   `RiveShader.aex` and copies it to `<After Effects>\Support Files\Plug-ins\Rive` (one administrator prompt).

The plugin renders with Direct3D 12 (any other backend wgpu finds if Direct3D 12 is missing).

### What has been checked on Windows

| Part | Checked | How |
|---|---|---|
| Python package, `ae2rml` on the demo | yes | CI on a Windows runner (`windows` job) |
| The plugin's GPU module (`rs_apply`, built with MSVC) | yes | CI: it renders every single-pass fxlib setting like `wgsl_apply.py` (software Direct3D 12) |
| The plugin in After Effects, the PiPL resource, the file dialog | no | needs a Windows machine with After Effects and the SDK |
| The panel, `ae build` / `ae pull` through `AfterFX.exe -r` | no | needs a Windows machine with After Effects |
| `install.ps1` end to end | no | parsed by PowerShell 7 only |

Please report what fails on your machine; the macOS path is the one measured in After Effects.
