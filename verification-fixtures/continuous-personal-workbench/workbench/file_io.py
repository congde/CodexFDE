"""Read local source with a bounded retry for Windows sharing violations."""
from pathlib import Path
import time
def read_bytes(path):
    for attempt in range(6):
        try:
            return Path(path).read_bytes()
        except PermissionError:
            if attempt == 5: raise
            time.sleep(.05 * 2**attempt)
def read_text(path,encoding='utf8'):
    return read_bytes(path).decode(encoding)
