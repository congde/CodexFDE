"""Process-owned local lease. A stale file is never treated as a live owner."""
from pathlib import Path
import os


class WorkbenchRuntimeInUse(RuntimeError):
    pass


class WorkbenchRuntimeLease:
    def __init__(self, runtime):
        self.path = Path(runtime).resolve() / 'workbench-service.lock'
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            handle.close()
            raise WorkbenchRuntimeInUse('这个运行目录已有工作台服务使用，请关闭原服务或选择另一个运行目录') from error
        self.file = handle
        return self

    def __exit__(self, *exc):
        if self.file is not None:
            try:
                self.file.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
            finally:
                self.file.close()
                self.file = None
