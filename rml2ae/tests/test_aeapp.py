"""aeapp helpers that need no After Effects: waiting for asynchronously written frames, the AppleScript app name,
the ExtendScript colour check (syntax), the modal-dialog watch, and on Windows the dialog probe against a real message
box shown by this process.

    python -m rml2ae.tests.test_aeapp
"""
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from rml2ae import aeapp  # noqa: E402


def dialog_watch():
    """DialogWatch reports a dialog seen twice in a row, ignores one that closes by itself, gives up on None."""
    seq = iter(["", "Save changes?", "", "After Effects error: x", "After Effects error: x"])
    w = aeapp.DialogWatch(every=0, grace=0, probe=lambda: next(seq))
    assert [w.check() for _ in range(5)] == ["", "", "", "", "After Effects error: x"]
    calls = []
    w = aeapp.DialogWatch(every=0, grace=0, probe=lambda: calls.append(1))
    assert w.check() == "" and w.check() == "" and len(calls) == 1
    w = aeapp.DialogWatch(every=0, grace=60, probe=lambda: "never asked")
    assert w.check() == ""


def windows_dialog():
    """Windows only: a real message box shown by this process is found, with its title and text."""
    import ctypes
    title, text = "rml2ae test", "Unable to execute script at line 3."
    th = threading.Thread(target=lambda: ctypes.windll.user32.MessageBoxW(None, text, title, 0))
    th.start()
    try:
        t0, hwnd = time.time(), 0
        while not hwnd and time.time() - t0 < 10:
            hwnd = ctypes.windll.user32.FindWindowW("#32770", title)
            time.sleep(0.1)
        assert hwnd, "the message box did not open"
        got = aeapp.windows_dialogs([os.getpid()])
        assert got is not None and title in got and text in got, got
        assert aeapp.windows_dialogs([0]) == ""
    finally:
        if hwnd:
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)        # WM_CLOSE
        th.join(10)
    assert aeapp.windows_dialogs([os.getpid()]) == ""


def main():
    tmp = tempfile.mkdtemp(prefix="aeapp_")
    try:
        # a file that appears late and grows for a while (what saveFrameToPng does) is waited for until it settles
        p = os.path.join(tmp, "frame.png")

        def writer():
            time.sleep(0.4)
            with open(p, "wb") as f:
                for _ in range(4):
                    f.write(b"x" * 1000)
                    f.flush()
                    time.sleep(0.3)
        th = threading.Thread(target=writer)
        th.start()
        missing = aeapp.wait_settled([p], timeout=10, stable=0.6)
        th.join()
        assert missing == [] and os.path.getsize(p) == 4000, (missing, os.path.getsize(p))
        # a file that never comes is reported missing after the timeout
        assert aeapp.wait_settled([os.path.join(tmp, "none.png")], timeout=0.5) == [os.path.join(tmp, "none.png")]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    os.environ["AE_APP"] = "Adobe After Effects 2025"
    assert aeapp.app_name() == "Adobe After Effects 2025"
    del os.environ["AE_APP"]
    assert aeapp.app_name().startswith("Adobe After Effects")
    if shutil.which("node"):
        js = aeapp.COLOR_SET_JSX + "\n" + aeapp.COLOR_CHECK_JSX + "\nvar cp = colorProblem();"
        r = subprocess.run(["node", "-e", "new Function(process.argv[1])", js], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    dialog_watch()
    if aeapp.WINDOWS:
        windows_dialog()
    print("test_aeapp: ok (wait_settled, app_name, colour check syntax, dialog watch"
          + (", Windows dialog probe)" if aeapp.WINDOWS else ")"))


if __name__ == "__main__":
    main()
