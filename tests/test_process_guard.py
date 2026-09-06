"""Real Windows process ownership; no Codex or student work is involved."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


@unittest.skipUnless(os.name == 'nt', 'Windows process lifetime contract')
class WindowsProcessGuardTests(unittest.TestCase):
    def test_owner_crash_terminates_both_child_and_grandchild(self):
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handles = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / 'child.py'
            script.write_text("import subprocess,sys,os,json,time\nfrom pathlib import Path\n"
                "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
                "Path('pids.json').write_text(json.dumps([os.getpid(),child.pid]))\n"
                "time.sleep(60)\n", encoding='utf-8')
            code = ("import sys,time\nfrom workbench.process_guard import spawn\n"
                    "process,owner,prefix=spawn([sys.executable,sys.argv[1]],sys.argv[2])\n"
                    "process.stdin.write(prefix);process.stdin.close()\n"
                    "time.sleep(60)\n")
            parent = subprocess.Popen([sys.executable, '-c', code, str(script), str(root)],
                                      cwd=Path(__file__).resolve().parent.parent,
                                      creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                deadline = time.monotonic() + 10
                while not (root / 'pids.json').exists() and time.monotonic() < deadline:
                    self.assertIsNone(parent.poll(), 'owner exited before children were ready')
                    time.sleep(.05)
                pids = json.loads((root / 'pids.json').read_text())
                for pid in pids:
                    handle = api.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
                    self.assertTrue(handle, 'child must be alive before owner crash')
                    handles.append(handle)
                    self.assertEqual(258, api.WaitForSingleObject(handle, 0))
                parent.kill(); parent.wait(timeout=5)
                for handle in handles:
                    self.assertEqual(0, api.WaitForSingleObject(handle, 5000),
                                     'child must terminate when owner closes unexpectedly')
            finally:
                if parent.poll() is None:
                    parent.kill(); parent.wait(timeout=5)
                for handle in handles:
                    api.CloseHandle(handle)
