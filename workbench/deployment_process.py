"""Process ownership for local services that outlive their startup probe."""
from functools import partial
from http.client import HTTPConnection, HTTPException, HTTPResponse
import io
import json
import math
import time
from urllib.parse import urlparse
from urllib.error import HTTPError
from urllib.request import build_opener, HTTPHandler, HTTPRedirectHandler, ProxyHandler

from .managed_process import ManagedProcess


_MAX_HEALTH_BODY_BYTES = 64 * 1024


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('健康检查响应读取超过总期限')
    return remaining


class _DeadlineReader(io.RawIOBase):
    """Every receive shares one deadline, including HTTP header/chunk parsing."""
    def __init__(self, sock, deadline):
        self.socket, self.deadline = sock, deadline
        self.raw = sock.makefile('rb', buffering=0)

    def readable(self):
        return True

    def readinto(self, buffer):
        self.socket.settimeout(_remaining(self.deadline))
        return self.raw.readinto(buffer)

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class _DeadlineResponse(HTTPResponse):
    def __init__(self, sock, *args, deadline, **kwargs):
        super().__init__(sock, *args, **kwargs)
        original = self.fp
        try:
            self.fp = io.BufferedReader(_DeadlineReader(sock, deadline))
        finally:
            original.close()


class _DeadlineHTTPHandler(HTTPHandler):
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def http_open(self, request):
        def connection(host, **options):
            options['timeout'] = min(options.get('timeout', _remaining(self.deadline)), _remaining(self.deadline))
            value = HTTPConnection(host, **options)
            value.response_class = partial(_DeadlineResponse, deadline=self.deadline)
            return value
        return self.do_open(connection, request)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe(url, health, timeout=2):
    """Read bounded local health JSON without redirects or renewed drip deadlines."""
    try:
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('健康检查总期限必须是正数')
        deadline = time.monotonic() + timeout
        target = urlparse(url)
        if target.scheme != 'http' or target.hostname not in {'localhost', '127.0.0.1', '::1'} or target.username or target.password or target.path not in {'', '/'} or target.query or target.fragment:
            raise ValueError('健康检查仅支持登记的本机HTTP根地址')
        path = health['path']
        if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or '?' in path or '#' in path:
            raise ValueError('健康检查路径不可改变登记地址')
        with build_opener(ProxyHandler({}), _NoRedirect(), _DeadlineHTTPHandler(deadline)).open(url.rstrip('/') + path, timeout=_remaining(deadline)) as response:
            length = response.headers.get('Content-Length')
            if length is not None and (int(length) < 0 or int(length) > _MAX_HEALTH_BODY_BYTES):
                raise ValueError('健康检查JSON超过64KiB体积上限')
            body = bytearray()
            while True:
                _remaining(deadline)
                # read(), including json.load(), can aggregate indefinitely while
                # a peer keeps renewing the socket timeout with tiny packets.
                chunk = response.read1(min(8192, _MAX_HEALTH_BODY_BYTES + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > _MAX_HEALTH_BODY_BYTES:
                    raise ValueError('健康检查JSON超过64KiB体积上限')
            _remaining(deadline)
            actual = json.loads(body)
            # Decoder recursion limits differ between supported Python versions.
            # Reject excessive nesting explicitly instead of trusting a 200 alone.
            stack = [(actual, 0)]
            while stack:
                node, depth = stack.pop()
                if depth > 64:
                    raise ValueError('健康检查JSON超过64层嵌套上限')
                if isinstance(node, dict):
                    stack.extend((value, depth + 1) for value in node.values())
                elif isinstance(node, list):
                    stack.extend((value, depth + 1) for value in node)
        if not isinstance(actual, dict):
            raise ValueError('健康检查须返回JSON对象')
        return {'passed': all(actual.get(k) == v for k, v in health['expected'].items()), 'actual': actual}
    except HTTPError as exc:
        # Refused redirects/errors can carry an unread response socket.
        try:
            return {'passed': False, 'error': str(exc)}
        finally:
            exc.close()
    except (OSError, ValueError, TypeError, KeyError, HTTPException, RecursionError) as exc:
        return {'passed': False, 'error': str(exc)}


class OwnedCommand(ManagedProcess):
    def __init__(self, command, cwd):
        from .codex_options import headless_environment
        super().__init__(command, cwd, env=headless_environment(), capture_limit=100_000)
        self.start()
