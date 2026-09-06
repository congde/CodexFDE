"""Cooperative cancellation scoped to the service worker, never the browser."""
import threading

local = threading.local()
delivery_lock = threading.Lock()


def cancelled():
    event = getattr(local, 'cancel_event', None)
    return bool(event and event.is_set())


def checkpoint():
    if cancelled():
        raise RuntimeError('任务已取消；原始输出与候选保留')
