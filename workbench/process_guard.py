"""Windows process-tree lifetime guard; wrapper waits for its owner's launch gate.

Job semantics: https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
This controls process lifetime, not filesystem permissions or task approval.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys


class WindowsJob:
    def __init__(self):
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('minimum', ctypes.c_size_t),
                        ('maximum', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_ulonglong * 6),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        for name, args, result in [
            ('CreateJobObjectW', [ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            ('SetInformationJobObject', [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            ('OpenProcess', [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ('AssignProcessToJobObject', [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            ('CloseHandle', [wintypes.HANDLE], wintypes.BOOL),
        ]:
            fn = getattr(self.api, name); fn.argtypes = args; fn.restype = result
        self.handle = self.api.CreateJobObjectW(None, None)  # Unnamed, not inheritable.
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway flags.
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error()); self.close(); raise error

    def assign(self, pid):
        process = self.api.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
        if not process:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if not self.api.AssignProcessToJobObject(self.handle, process):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            self.api.CloseHandle(process)

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            if not self.api.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())


def spawn(command, cwd):
    """Return (process, owner, stdin prefix). No command runs before assignment."""
    owner = WindowsJob() if os.name == 'nt' else None
    actual = [sys.executable, '-X', 'utf8', '-u', str(Path(__file__).resolve())] if owner else command
    try:
        process = subprocess.Popen(actual, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace',
                                   **({'creationflags': subprocess.CREATE_NO_WINDOW} if owner else {}))
        if owner:
            try:
                owner.assign(process.pid)
            except BaseException:
                process.kill(); process.wait(timeout=5)
                for pipe in (process.stdin, process.stdout, process.stderr):
                    pipe.close()
                raise
        return process, owner, json.dumps(command) + '\n' if owner else ''
    except BaseException:
        if owner:
            owner.close()
        raise


def main():
    # EOF before the gate is an owner failure; never launch an unowned command.
    header = sys.stdin.readline()
    if not header:
        return 125
    command = json.loads(header)
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) for arg in command):
        return 125
    prompt = sys.stdin.read()
    return subprocess.run(command, input=prompt, text=True, encoding='utf-8', errors='replace',
                          **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {})).returncode


if __name__ == '__main__':
    raise SystemExit(main())
