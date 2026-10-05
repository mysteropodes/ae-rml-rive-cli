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
    subprocess.Popen(["osascript", "-e", f'tell application "{app_name()}" to DoScriptFile "{jsx}"'],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def app_name():
    """macOS: the application name AppleScript knows: AE_APP if set, else the newest installed folder's name
    ("Adobe After Effects 2025")"""
    return os.environ.get("AE_APP") or (os.path.basename(APP.rstrip("/")) if APP else "Adobe After Effects 2026")


def wait_settled(paths, timeout=300, stable=1.5):
    """CompItem.saveFrameToPng returns before the PNG is on disk (it is written asynchronously): wait until every
    file exists, is non-empty and has stopped growing for `stable` s. Returns the paths still missing."""
    import time
    t0 = time.time()
    while time.time() - t0 < timeout:
        sizes = [os.path.getsize(p) if os.path.exists(p) else -1 for p in paths]
        if all(s > 0 for s in sizes):
            time.sleep(stable)
            if sizes == [os.path.getsize(p) for p in paths]:
                return []
        time.sleep(0.5)
    return [p for p in paths if not os.path.exists(p) or os.path.getsize(p) == 0]


def shader_registry():
    """The Rive Shader plugin's registry (id -> .wgsl path), the same file the plugin reads (RsRegistry.cpp)."""
    if WINDOWS:
        return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "RiveShader", "shaders.tsv")
    return os.path.expanduser("~/Library/Application Support/RiveShader/shaders.tsv")


# ExtendScript: why the open project's frames cannot serve as display-referred references (empty when they can).
# In a colour-managed project CompItem.saveFrameToPng writes the linear working buffer (scaled), not what the viewer
# shows; above 8 bpc the values are not the 8-bit ones the comparisons and fxlib references assume.
COLOR_CHECK_JSX = (
    'function colorProblem() {'
    ' var p = app.project, ws = "", why = [];'
    ' try { ws = String(p.workingSpace); } catch (e) {}'
    ' if (ws != "" && ws != "None") why.push("working space " + ws);'
    ' try { if (p.linearBlending) why.push("linear blending"); } catch (e) {}'
    ' try { if (p.bitsPerChannel != 8) why.push(p.bitsPerChannel + " bpc"); } catch (e) {}'
    ' return why.join(", "); }')

# ExtendScript: put the open project in the state the fxlib references assume (8 bpc, no colour management)
COLOR_SET_JSX = (
    'try { app.project.colorManagementSystem = 0; } catch (e) {}'
    ' try { app.project.workingSpace = "None"; } catch (e) {}'
    ' try { app.project.linearBlending = false; } catch (e) {}'
    ' try { app.project.linearizeWorkingSpace = false; } catch (e) {}'
    ' try { app.project.bitsPerChannel = 8; } catch (e) {}')
