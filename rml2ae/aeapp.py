"""Where After Effects is and how to talk to it, on macOS and Windows.

macOS:   /Applications/Adobe After Effects <version>/, scripts sent with AppleScript (DoScriptFile), aerender next to
         the app.
Windows: C:\\Program Files\\Adobe\\Adobe After Effects <version>\\Support Files\\, scripts sent with
         `AfterFX.exe -r <file.jsx>` (handed to the running instance), aerender.exe in the same folder.
"""
import glob
import os
import shutil
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


def wait_settled(paths, timeout=300, stable=1.5, watch=None):
    """CompItem.saveFrameToPng returns before the PNG is on disk (it is written asynchronously): wait until every
    file exists, is non-empty and has stopped growing for `stable` s. Returns the paths still missing. With a
    DialogWatch, raises RuntimeError when After Effects stops on a modal dialog."""
    import time
    t0 = time.time()
    while time.time() - t0 < timeout:
        dialog = watch.check() if watch else ""
        if dialog:
            raise RuntimeError("After Effects shows a dialog: " + dialog)
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


# --- modal dialogs ------------------------------------------------------------------------------------------------
# A script error, a missing-file warning or a "save changes?" question stops After Effects on a modal dialog until
# someone clicks: the script's log never comes and the caller would wait for its whole timeout. These probes read the
# dialogs' text so the wait can stop at once and say why.

# Windows: the visible dialog-class (#32770) windows of the given processes that are modal (their owner is disabled,
# or they have none), with their title and the text of their Static and Edit controls (WM_GETTEXT: GetWindowText does
# not read another process' controls)
_WIN_DIALOGS_CS = r'''
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public static class RmlAeDialogs {
    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr h, EnumProc f, IntPtr l);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern bool IsWindowEnabled(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr h, uint cmd);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr SendMessageTimeout(IntPtr h, uint msg, IntPtr w, StringBuilder l, uint flags, uint ms, out IntPtr r);
    static string ClassOf(IntPtr h) { StringBuilder s = new StringBuilder(64); GetClassName(h, s, 64); return s.ToString(); }
    static string TextOf(IntPtr h) {
        StringBuilder s = new StringBuilder(4096); IntPtr r;
        SendMessageTimeout(h, 0x000D, new IntPtr(4096), s, 0x0002, 1000, out r);     // WM_GETTEXT, SMTO_ABORTIFHUNG
        return s.ToString().Trim();
    }
    public static string Find(int[] pids) {
        Dictionary<uint, bool> want = new Dictionary<uint, bool>(); foreach (int p in pids) want[(uint)p] = true;
        List<string> found = new List<string>();
        EnumWindows(delegate(IntPtr h, IntPtr l) {
            uint pid; GetWindowThreadProcessId(h, out pid);
            if (!want.ContainsKey(pid) || !IsWindowVisible(h) || ClassOf(h) != "#32770") return true;
            IntPtr owner = GetWindow(h, 4);                                            // GW_OWNER
            if (owner != IntPtr.Zero && IsWindowEnabled(owner)) return true;          // not modal
            List<string> parts = new List<string>(); string title = TextOf(h); if (title != "") parts.Add(title);
            EnumChildWindows(h, delegate(IntPtr c, IntPtr l2) {
                string k = ClassOf(c);
                if ((k == "Static" || k == "Edit") && IsWindowVisible(c)) { string t = TextOf(c); if (t != "") parts.Add(t); }
                return true; }, IntPtr.Zero);
            found.Add(string.Join(" / ", parts.ToArray())); return true; }, IntPtr.Zero);
        return string.Join(" || ", found.ToArray());
    }
}
'''

# macOS: the dialog windows of After Effects as System Events sees them (needs the Accessibility permission for the
# terminal that runs rml2ae: System Settings > Privacy & Security > Accessibility)
_MAC_DIALOGS_AS = '''
set found to {}
tell application "System Events"
    repeat with p in (every process whose name starts with "After Effects")
        repeat with w in (every window of p)
            set sr to ""
            try
                set sr to subrole of w
            end try
            if sr is "AXDialog" or sr is "AXSystemDialog" then
                set parts to {}
                try
                    set t to name of w
                    if t is not missing value and t is not "" then set end of parts to t
                end try
                try
                    repeat with s in (value of every static text of w)
                        if s is not missing value and (s as text) is not "" then set end of parts to (s as text)
                    end repeat
                end try
                set AppleScript's text item delimiters to " / "
                set end of found to (parts as text)
            end if
        end repeat
    end repeat
end tell
set AppleScript's text item delimiters to " || "
return found as text
'''


def _powershell(script, timeout=30):
    import base64
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return None
    enc = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc],
                           capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode("utf-8", "replace").strip()


def windows_dialogs(pids):
    """Windows: the text of the modal dialogs the processes `pids` show ("" when none), None when PowerShell fails."""
    ps = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8\n"
          "Add-Type -TypeDefinition @'\n" + _WIN_DIALOGS_CS.strip() + "\n'@\n"
          "[RmlAeDialogs]::Find(@(" + ",".join(str(int(p)) for p in pids) + "))")
    return _powershell(ps)


def modal_dialog():
    """The text of the modal dialog After Effects is waiting on ("" when none is shown), None when it cannot tell
    (macOS without the Accessibility permission, no PowerShell, another platform)."""
    if WINDOWS:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq AfterFX.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True)
        pids = [int(row.split('","')[1]) for row in r.stdout.splitlines() if row.startswith('"AfterFX.exe"')]
        return windows_dialogs(pids) if pids else ""
    if sys.platform != "darwin":
        return None
    try:
        r = subprocess.run(["osascript", "-e", _MAC_DIALOGS_AS], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


class DialogWatch:
    """For wait loops: check() looks at After Effects' dialogs at most every `every` s, after `grace` s, and returns the
    dialog's text once the same one has been seen twice in a row (a dialog that closes by itself, such as a progress
    window, is not reported), else "". It stops probing for good when the probe cannot tell (returns None)."""

    def __init__(self, every=10.0, grace=10.0, probe=None):
        import time
        self.every, self.probe, self.last, self.off = every, probe or modal_dialog, "", False
        self.next = time.time() + grace

    def check(self):
        import time
        if self.off or time.time() < self.next:
            return ""
        self.next = time.time() + self.every
        cur = self.probe()
        if cur is None:
            self.off = True
            return ""
        seen, self.last = (cur if cur and cur == self.last else ""), cur
        return seen
