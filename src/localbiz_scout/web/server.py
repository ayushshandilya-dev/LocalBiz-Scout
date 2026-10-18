"""A tiny, loopback-only HTTP server for the LocalBiz Scout web interface.

Design constraints, deliberate and documented:

* **Loopback only.** The bind address is validated to be a loopback address
  (``127.0.0.0/8``, ``::1`` or the name ``localhost``); anything else is
  refused, so the server is never reachable from the network.
* **Standard library only.** No third-party web framework, no dependencies.
* **No persistence.** Uploads are parsed in memory, scanned, and discarded.
  Nothing is written to disk and no sessions or caches are kept.
* **Fixed routes only.** Requests are matched against exact paths; request
  paths are never mapped to filesystem paths, so path traversal is impossible.
* **CSRF.** A per-process random token is embedded in the form and required on
  POST (double-submit style). A cross-origin page cannot read our HTML and so
  cannot obtain the token. Because the server is loopback-only and stateless,
  this lightweight scheme is proportionate; it is not a substitute for
  authentication, which this tool does not have.
* **Loopback ``Host``.** The ``Host`` header is validated to a loopback name or
  address, which blocks the common DNS-rebinding case where a hostile name
  resolves to loopback. Validation is purely syntactic (no DNS resolution) and
  does not claim complete rebinding immunity.
* **Bounded reads.** Sockets carry an idle read timeout *and* request bodies are
  read against a total wall-clock deadline, so a client cannot keep a handler
  occupied indefinitely by dribbling bytes just under the idle timeout. Only
  plain ``Content-Length`` framing is accepted; ``Transfer-Encoding`` is refused.
"""

from __future__ import annotations

import email
import ipaddress
import secrets
import socket
import sys
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..errors import InputError
from . import view

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "MAX_UPLOAD_BYTES",
    "ScanHTTPServer",
    "create_server",
    "is_oversized_content_length",
    "resolve_bind",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

# Reject uploads larger than this before reading the body into memory.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB

# Socket read timeout, in seconds. A client that stalls mid-request is dropped
# rather than holding a worker thread open forever. Overridable for tests.
REQUEST_TIMEOUT = 30

# Multipart framing (boundaries, field headers) adds a small amount on top of
# the file itself; allow that when sanity-checking Content-Length, then enforce
# the exact file limit on the parsed part.
_MULTIPART_SLACK = 64 * 1024

# Size of each read when pulling a request body off the socket. Small enough to
# check the total deadline frequently, large enough to stay efficient.
_BODY_READ_CHUNK = 64 * 1024

# Bundled sample, resolved relative to the repository root. Fixed path; never
# derived from a request.
_SAMPLE_CSV_PATH = Path(__file__).resolve().parents[3] / "data" / "sample_businesses.csv"

_ALLOWED_UPLOAD_SUFFIXES = ("", ".csv", ".txt")


def is_oversized_content_length(length: int) -> bool:
    """True when a request body is too large to read into memory."""

    return length > MAX_UPLOAD_BYTES + _MULTIPART_SLACK


def _is_loopback_host(host: str) -> bool:
    if not isinstance(host, str) or not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _validate_request_timeout(value: object) -> int:
    """Return ``value`` if it is a positive integer number of seconds."""

    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InputError(
            f"invalid request timeout {value!r}; expected a positive number of seconds"
        )
    return value


def _is_multipart_form_data(content_type: str) -> bool:
    """True only for the exact ``multipart/form-data`` media type.

    The media type is compared case-insensitively and any parameters (such as
    ``boundary``) are ignored, but near-misses like ``multipart/form-dataXYZ``
    are rejected rather than accepted by a prefix match.
    """

    media_type = content_type.split(";", 1)[0].strip().lower()
    return media_type == "multipart/form-data"


def _valid_port(text: str) -> bool:
    if not text.isdigit():
        return False
    return 0 <= int(text) <= 65535


def host_header_ok(raw: str | None) -> bool:
    """Validate a ``Host`` header against loopback names/addresses.

    Purely syntactic (never resolves DNS). Accepts ``name``,
    ``name:port``, ``[::1]`` and ``[::1]:port``; rejects anything containing
    whitespace, control characters, path/authority separators, or a non-loopback
    hostname.
    """

    if not raw:
        return False
    host = raw.strip()
    if not host or any(ch <= " " or ch == "\x7f" for ch in host):
        return False
    if any(ch in host for ch in "/\\?#@*,"):
        return False
    if ".." in host:
        return False

    if host.startswith("["):
        end = host.find("]")
        if end == -1:
            return False
        hostname = host[1:end]
        rest = host[end + 1:]
        if rest and (not rest.startswith(":") or not _valid_port(rest[1:])):
            return False
    elif ":" in host:
        hostname, _, port = host.rpartition(":")
        if not _valid_port(port):
            return False
    else:
        hostname = host

    return _is_loopback_host(hostname)


def resolve_bind(host: str, port: int) -> tuple[str, int]:
    """Validate a bind address, returning ``(host, port)``.

    Raises :class:`InputError` for non-loopback hosts or out-of-range ports so
    the service can never be exposed on the network.
    """

    if not _is_loopback_host(host):
        raise InputError(
            f"refusing to bind to non-loopback host {host!r}; "
            f"use {DEFAULT_HOST} (or ::1 / localhost)"
        )
    if not isinstance(port, int) or isinstance(port, bool) or not (0 <= port <= 65535):
        raise InputError(f"invalid port {port!r}; expected 0-65535")
    return host, port


def _valid_upload_name(filename: str | None) -> bool:
    """Reject filenames whose suffix is clearly not CSV.

    The extension is only a coarse filter; the real gate is content validation
    in the loader, so a misnamed-yet-valid file is still rejected on extension
    but a valid ``.csv`` with wrong contents is rejected on parse.
    """

    if not filename:
        return True
    suffix = Path(filename).suffix.lower()
    return suffix in _ALLOWED_UPLOAD_SUFFIXES


def _safe_basename(filename: str | None) -> str:
    if not filename:
        return "uploaded.csv"
    # Strip any directory component (POSIX or Windows) and control characters.
    name = filename.replace("\\", "/").split("/")[-1]
    name = "".join(ch for ch in name if ch >= " " and ch != "\x7f")
    return name[:128] or "uploaded.csv"


@dataclass(frozen=True)
class _FormData:
    """Parsed multipart form: scalar fields plus at most one file part.

    ``duplicate_fields`` records scalar names that appeared more than once so
    the caller can reject ambiguous submissions (e.g. two ``csrf_token`` fields)
    instead of silently taking the last one. ``file_count`` is the number of
    file parts seen, so multiple uploads can be rejected.
    """

    values: dict[str, str]
    filename: str | None
    file_bytes: bytes | None
    duplicate_fields: frozenset[str]
    file_count: int


def _parse_multipart(content_type: str, body: bytes) -> _FormData | None:
    """Parse a multipart/form-data body.

    Returns ``None`` when the body is not multipart or its boundary is missing,
    so the caller rejects it rather than treating it as an empty form.
    """

    message = email.message_from_bytes(
        b"Content-Type: " + content_type.encode("latin-1", "replace")
        + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
    )

    if not message.is_multipart():
        return None

    fields: dict[str, str] = {}
    duplicate_fields: set[str] = set()
    file_bytes: bytes | None = None
    filename: str | None = None
    file_count = 0

    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        if "form-data" not in disposition:
            continue
        name = part.get_param("name", header="content-disposition")
        if name is None:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            payload = b""
        part_filename = part.get_filename()
        if part_filename is not None:
            filename = part_filename
            file_bytes = payload
            file_count += 1
        else:
            if name in fields:
                duplicate_fields.add(name)
            fields[name] = payload.decode("utf-8", "replace")

    return _FormData(fields, filename, file_bytes, frozenset(duplicate_fields), file_count)


class ScanHTTPServer(ThreadingHTTPServer):
    """HTTP server carrying the scan configuration and CSRF token."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[BaseHTTPRequestHandler],
        *,
        csrf_token: str,
        config: Any = None,
        sample_bytes: bytes | None = None,
        request_timeout: int = REQUEST_TIMEOUT,
    ) -> None:
        # Validate before binding so a bad timeout never opens a socket.
        timeout = _validate_request_timeout(request_timeout)
        super().__init__(server_address, handler_cls)
        self.csrf_token = csrf_token
        self.config = config
        self.sample_bytes = sample_bytes
        self.request_timeout = timeout


class _Handler(BaseHTTPRequestHandler):
    server_version = "LocalBizScout-web"
    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT

    def version_string(self) -> str:
        # Omit the interpreter version the base class would append to sys_version.
        return self.server_version

    def setup(self) -> None:
        # Apply the server's timeout before the socket is wrapped by the base
        # class, which is where StreamRequestHandler installs it.
        configured = getattr(self.server, "request_timeout", None)
        if configured is not None:
            self.timeout = configured
        super().setup()

    # -- logging: method/path/status only; never record fields -----------------

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        message = format % args
        clean = "".join(ch for ch in message if ch >= " " or ch == "\t")
        sys.stderr.write(f"[localbiz-scout-web] {clean}\n")

    # -- helpers ---------------------------------------------------------------

    def _send_html(self, status: HTTPStatus, body: str, *, close: bool = False) -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if close:
            # Used when the request body was intentionally not consumed.
            self.close_connection = True
            self.send_header("Connection", "close")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_bytes(
        self, status: HTTPStatus, content_type: str, data: bytes, disposition: str | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'",
        )

    def _csrf_token(self) -> str:
        return self.server.csrf_token  # type: ignore[attr-defined]

    def _has_sample(self) -> bool:
        return self.server.sample_bytes is not None  # type: ignore[attr-defined]

    def _host_header_ok(self) -> bool:
        # Reject a missing Host and any request that repeats it, rather than
        # silently trusting the first value.
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1:
            return False
        return host_header_ok(hosts[0])

    def _read_body(self, length: int) -> bytes:
        """Read exactly ``length`` body bytes under a total wall-clock deadline.

        The socket idle timeout still applies between reads; this adds a cap on
        the *whole* read so a client cannot hold the handler open by sending
        small chunks just under the idle timeout. Raises :class:`TimeoutError`
        when the deadline elapses.
        """

        if length <= 0:
            return b""
        deadline = time.monotonic() + self.server.request_timeout  # type: ignore[attr-defined]
        chunks: list[bytes] = []
        remaining = length
        try:
            while remaining > 0:
                budget = deadline - time.monotonic()
                if budget <= 0:
                    raise TimeoutError("request body read deadline exceeded")
                self.connection.settimeout(min(self.timeout, budget))
                chunk = self.rfile.read1(min(remaining, _BODY_READ_CHUNK))
                if not chunk:
                    break  # client closed early; caller handles the short body
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            self.connection.settimeout(self.timeout)
        return b"".join(chunks)

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        if origin == "null":
            return False
        host = urlsplit(origin).hostname or ""
        return _is_loopback_host(host)

    def _content_length(self) -> int | None:
        raw = self.headers.get("Content-Length")
        if raw is None:
            return None
        try:
            length = int(raw)
        except ValueError:
            return None
        return length if length >= 0 else None

    def _reject(self, status: HTTPStatus, message: str, *, close: bool = False) -> None:
        """Send an error page, tolerating a client that already hung up."""

        try:
            self._send_html(
                status,
                view.render_error_page(
                    message,
                    csrf_token=self._csrf_token(),
                    has_sample=self._has_sample(),
                ),
                close=close,
            )
        except OSError:
            self.close_connection = True

    # -- routes ----------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_header_ok():
            self._reject(HTTPStatus.BAD_REQUEST, "Invalid Host header.", close=True)
            return
        try:
            path = urlsplit(self.path).path
            if path == "/":
                self._send_html(
                    HTTPStatus.OK,
                    view.render_upload_page(
                        csrf_token=self._csrf_token(),
                        has_sample=self._has_sample(),
                    ),
                )
            elif path == "/sample.csv":
                self._serve_sample()
            else:
                self._send_html(
                    HTTPStatus.NOT_FOUND,
                    view.render_error_page(
                        "Page not found.",
                        csrf_token=self._csrf_token(),
                        has_sample=self._has_sample(),
                    ),
                )
        except (socket.timeout, TimeoutError):
            self._reject(HTTPStatus.REQUEST_TIMEOUT, "Request timed out.", close=True)
        except Exception:  # noqa: BLE001 - never leak a traceback to the client
            self._reject(HTTPStatus.INTERNAL_SERVER_ERROR, "An internal error occurred.", close=True)

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_header_ok():
            self._reject(HTTPStatus.BAD_REQUEST, "Invalid Host header.", close=True)
            return
        try:
            path = urlsplit(self.path).path
            if path != "/scan":
                self._send_html(
                    HTTPStatus.NOT_FOUND,
                    view.render_error_page(
                        "Page not found.",
                        csrf_token=self._csrf_token(),
                        has_sample=self._has_sample(),
                    ),
                    close=True,
                )
                return
            self._handle_scan()
        except (socket.timeout, TimeoutError):
            self._reject(HTTPStatus.REQUEST_TIMEOUT, "Request timed out.", close=True)
        except Exception:  # noqa: BLE001 - never leak a traceback to the client
            self._reject(HTTPStatus.INTERNAL_SERVER_ERROR, "An internal error occurred.", close=True)

    def _serve_sample(self) -> None:
        data = self.server.sample_bytes  # type: ignore[attr-defined]
        if data is None:
            self._send_html(
                HTTPStatus.NOT_FOUND,
                view.render_error_page(
                    "Bundled sample is unavailable.",
                    csrf_token=self._csrf_token(),
                    has_sample=False,
                ),
            )
            return
        self._send_bytes(
            HTTPStatus.OK,
            "text/csv; charset=utf-8",
            data,
            disposition='attachment; filename="sample_businesses.csv"',
        )

    def _handle_scan(self) -> None:
        content_type = self.headers.get("Content-Type") or ""
        if not _is_multipart_form_data(content_type):
            self._reject(HTTPStatus.BAD_REQUEST, "Expected a multipart form upload.", close=True)
            return

        # Only plain Content-Length framing is understood. Refuse
        # Transfer-Encoding outright rather than reading a body we cannot frame;
        # a request that carries both is ambiguous and rejected as malformed.
        if self.headers.get("Transfer-Encoding") is not None:
            if self.headers.get("Content-Length") is not None:
                self._reject(HTTPStatus.BAD_REQUEST, "Ambiguous request framing.", close=True)
            else:
                self._reject(
                    HTTPStatus.NOT_IMPLEMENTED, "Transfer-Encoding is not supported.", close=True
                )
            return

        length = self._content_length()
        if length is None:
            self._reject(HTTPStatus.LENGTH_REQUIRED, "Missing Content-Length.", close=True)
            return

        # Reject oversized uploads from the declared length, before reading.
        if is_oversized_content_length(length):
            self._reject(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "Upload exceeds the 5 MiB limit.",
                close=True,
            )
            return

        try:
            body = self._read_body(length)
        except (socket.timeout, TimeoutError):
            self._reject(HTTPStatus.REQUEST_TIMEOUT, "Timed out reading the upload.", close=True)
            return

        form = _parse_multipart(content_type, body)
        if form is None:
            self._reject(HTTPStatus.BAD_REQUEST, "Malformed multipart upload.", close=True)
            return

        # Reject ambiguous submissions: more than one uploaded file, or a
        # security-relevant field supplied twice (last-wins is unsafe here).
        if form.file_count > 1 or form.duplicate_fields & {"csrf_token", "action"}:
            self._reject(HTTPStatus.BAD_REQUEST, "Malformed form submission.", close=True)
            return

        token = form.values.get("csrf_token", "")
        if not secrets.compare_digest(
            token.encode("utf-8"), self._csrf_token().encode("utf-8")
        ):
            self._reject(
                HTTPStatus.FORBIDDEN,
                "This form submission could not be verified. Reload the page and try again.",
            )
            return

        if not self._origin_ok():
            self._reject(HTTPStatus.FORBIDDEN, "Cross-origin requests are not allowed.")
            return

        action = form.values.get("action", "scan")
        if action == "sample":
            data = self.server.sample_bytes  # type: ignore[attr-defined]
            if data is None:
                self._render_error("Bundled sample is unavailable.")
                return
            csv_bytes, display_name = data, "sample_businesses.csv"
        else:
            if form.file_bytes is None:
                self._render_error("Choose a CSV file to scan.")
                return
            if len(form.file_bytes) > MAX_UPLOAD_BYTES:
                self._reject(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Upload exceeds the 5 MiB limit.")
                return
            if not _valid_upload_name(form.filename):
                self._render_error("Only .csv files are accepted.")
                return
            csv_bytes, display_name = form.file_bytes, _safe_basename(form.filename)

        try:
            result = view.analyze_upload(csv_bytes, self.server.config)  # type: ignore[attr-defined]
        except InputError as error:
            self._render_error(str(error))
            return

        self._send_html(
            HTTPStatus.OK,
            view.render_results_page(
                result,
                filename=display_name,
                csrf_token=self._csrf_token(),
                has_sample=self._has_sample(),
            ),
        )

    def _render_error(self, message: str) -> None:
        self._reject(HTTPStatus.BAD_REQUEST, message)


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    config: Any = None,
    request_timeout: int = REQUEST_TIMEOUT,
) -> ScanHTTPServer:
    """Create (but do not start) a validated, loopback-only server.

    Raises :class:`InputError` for a non-loopback host or invalid port.
    """

    bind_host, bind_port = resolve_bind(host, port)
    csrf_token = secrets.token_urlsafe(32)
    sample_bytes: bytes | None = None
    if _SAMPLE_CSV_PATH.is_file():
        sample_bytes = _SAMPLE_CSV_PATH.read_bytes()
    return ScanHTTPServer(
        (bind_host, bind_port),
        _Handler,
        csrf_token=csrf_token,
        config=config,
        sample_bytes=sample_bytes,
        request_timeout=request_timeout,
    )
