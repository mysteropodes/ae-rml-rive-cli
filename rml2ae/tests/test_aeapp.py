"""aeapp helpers that need no After Effects: waiting for asynchronously written frames, the AppleScript app name,
and the ExtendScript colour check (syntax).

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
    print("test_aeapp: ok (wait_settled, app_name, colour check syntax)")


if __name__ == "__main__":
    main()
