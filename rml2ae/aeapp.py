"""Where After Effects is and how to talk to it, on macOS and Windows.

macOS:   /Applications/Adobe After Effects <version>/, scripts sent with AppleScript (DoScriptFile), aerender next to
         the app.
Windows: C:\\Program Files\\Adobe\\Adobe After Effects <version>\\Support Files\\, scripts sent with
         `AfterFX.exe -r <file.jsx>` (handed to the running instance), aerender.exe in the same folder.
"""
import glob
import os
import subprocess
import sys

WINDOWS = sys.platform.startswith("win")


def _apps():
    if WINDOWS:
        base = os.environ.get("ProgramFiles", r"C:\Program Files")
        return sorted(glob.glob(os.path.join(base, "Adobe", "Adobe After Effects *", "Support Files") + os.sep))
    return sorted(glob.glob("/Applications/Adobe After Effects */"))


APPS = _apps()
APP = APPS[-1] if APPS else None                 # the newest version: its folder (Windows: its Support Files folder)
WHERE = r"%ProgramFiles%\Adobe" if WINDOWS else "/Applications"
AERENDER = os.path.join(APP, "aerender.exe" if WINDOWS else "aerender") if APP else None
AFTERFX = os.path.join(APP, "AfterFX.exe") if (APP and WINDOWS) else None
PLUGINS = os.path.join(APP, "Plug-ins") if APP else None
PANELS = os.path.join(APP, "Scripts", "ScriptUI Panels") if APP else None
PLUGIN_NAME = "RiveShader.aex" if WINDOWS else "RiveShader.plugin"


def running():
    if WINDOWS:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq AfterFX.exe", "/NH"], capture_output=True, text=True)
        return "AfterFX.exe" in r.stdout
    r = subprocess.run(["pgrep", "-f", "Adobe After Effects .*app/Contents/MacOS/After Effects$"], capture_output=True, text=True)
    return bool(r.stdout.strip())


def run_script(jsx):
    """Hand a .jsx to the running After Effects (returns at once; the script reports through its own log file)."""
    if WINDOWS:
        if not AFTERFX:
            raise RuntimeError(f"After Effects not found in {WHERE}")
        subprocess.Popen([AFTERFX, "-r", jsx], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return
    subprocess.Popen(["osascript", "-e", f'tell application "Adobe After Effects 2026" to DoScriptFile "{jsx}"'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def shader_registry():
    """The Rive Shader plugin's registry (id -> .wgsl path), the same file the plugin reads (RsRegistry.cpp)."""
    if WINDOWS:
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RiveShader", "shaders.tsv")
    return os.path.expanduser("~/Library/Application Support/RiveShader/shaders.tsv")
