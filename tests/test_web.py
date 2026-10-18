"""Tests for the local web interface.

These exercise the in-memory analysis entry point and the loopback-only server
directly (no browser, no external services). The server is started on an
ephemeral port in a background thread and shut down in a fixture so no process
is left running.
"""

from __future__ import annotations

import http.client
import socket
import threading
import time
from pathlib import Path

import pytest

from localbiz_scout import web
from localbiz_scout.cli import build_parser
from localbiz_scout.errors import InputError
from localbiz_scout.loader import load_records
from localbiz_scout.rules import scan
from localbiz_scout.rules.config import DEFAULT_CONFIG

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE = REPO_ROOT / "data" / "sample_businesses.csv"

CLEAN_CSV = (
    b"business_id,name,address,city,state,postal_code,phone,website\n"
    b"b1,Acme Coffee,1 Main Street,Austin,TX,78701,512-555-0100,https://example.com\n"
)


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def server():
    srv = web.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


@pytest.fixture
def timeout_server():
    """A server whose read timeout is short enough to exercise 408 handling."""

    srv = web.create_server("127.0.0.1", 0, request_timeout=1)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


def _multipart(fields, files, boundary="----localbizscoutboundary"):
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    for key, (filename, data) in files.items():
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; '
                f'filename="{filename}"\r\nContent-Type: text/csv\r\n\r\n'
            ).encode("utf-8")
            + data
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _multipart_body(parts, boundary="----localbizscoutboundary"):
    """Build a multipart body from an ordered list of parts.

    Each part is ``(name, value, filename)``; ``filename`` is ``None`` for a
    scalar field. Unlike :func:`_multipart` this permits repeated names and
    multiple file parts, which the server must reject.
    """

    chunks: list[bytes] = []
    for name, value, filename in parts:
        if filename is None:
            chunks.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n".encode("utf-8")
            )
        else:
            chunks.append(
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\nContent-Type: text/csv\r\n\r\n'
                ).encode("utf-8")
                + value
                + b"\r\n"
            )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _request(server, method, path, *, body=None, content_type=None, headers=None):
    host, port = server.server_address[:2]
    conn = http.client.HTTPConnection(host, port, timeout=10)
    request_headers = {}
    if content_type:
        request_headers["Content-Type"] = content_type
    if headers:
        request_headers.update(headers)
    conn.request(method, path, body=body, headers=request_headers)
    response = conn.getresponse()
    payload = response.read()
    headers_out = {k.lower(): v for k, v in response.getheaders()}
    conn.close()
    return response.status, headers_out, payload


def _raw_exchange(server, request: bytes, *, read_seconds: float = 10.0) -> bytes:
    """Send raw bytes and return whatever the server writes back."""

    host, port = server.server_address[:2]
    with socket.create_connection((host, port), timeout=read_seconds) as sock:
        sock.sendall(request)
        sock.shutdown(socket.SHUT_WR)
        sock.settimeout(read_seconds)
        data = b""
        while True:
            try:
                chunk = sock.recv(4096)
            except (socket.timeout, TimeoutError):
                break
            if not chunk:
                break
            data += chunk
    return data


def _post_csv(server, csv_bytes, filename="records.csv", action="scan", token=None):
    body, content_type = _multipart(
        {"action": action, "csrf_token": token if token is not None else server.csrf_token},
        {"csv": (filename, csv_bytes)},
    )
    return _request(server, "POST", "/scan", body=body, content_type=content_type)


# --------------------------------------------------------------------------- #
# analyze_upload: parity, decoding, validation
# --------------------------------------------------------------------------- #


def test_analyze_upload_matches_engine():
    result = web.analyze_upload(SAMPLE.read_bytes())

    loaded = load_records(SAMPLE, DEFAULT_CONFIG.id_field)
    engine = scan(loaded.records, DEFAULT_CONFIG)

    assert result.record_count == 17
    assert result.finding_count == len(engine.findings)
    assert result.duplicate_count == len(engine.duplicate_candidates)
    assert [d.reason for d in result.duplicates] == [
        c.reason for c in engine.duplicate_candidates
    ]
    assert [f.business_id for f in result.findings] == [
        finding.business_id for finding in engine.findings
    ]


def test_analyze_upload_handles_utf8_bom():
    result = web.analyze_upload(b"\xef\xbb\xbfbusiness_id,name\nb1,Acme\n")
    assert result.record_count == 1
    assert result.columns[0] == "business_id"


def test_analyze_upload_rejects_empty_input():
    with pytest.raises(InputError, match="empty"):
        web.analyze_upload(b"")


def test_analyze_upload_requires_identifier_column():
    with pytest.raises(InputError, match="business_id"):
        web.analyze_upload(b"name,city\nAcme,Austin\n")


def test_analyze_upload_rejects_ragged_rows():
    with pytest.raises(InputError, match="malformed"):
        web.analyze_upload(b"business_id,name\nb1,Acme,extra\n")


def test_analyze_upload_rejects_invalid_encoding():
    with pytest.raises(InputError, match="UTF-8"):
        web.analyze_upload(b"\xff\xfe\x00\x01")


# --------------------------------------------------------------------------- #
# Upload limits
# --------------------------------------------------------------------------- #


def test_content_length_limit_predicate():
    assert web.is_oversized_content_length(0) is False
    assert web.is_oversized_content_length(web.MAX_UPLOAD_BYTES) is False
    assert web.is_oversized_content_length(web.MAX_UPLOAD_BYTES + 1024 * 1024) is True


def test_oversized_upload_is_rejected_before_reading(server):
    host, port = server.server_address
    declared = web.MAX_UPLOAD_BYTES + 10 * 1024 * 1024
    with socket.create_connection((host, port), timeout=10) as sock:
        request = (
            "POST /scan HTTP/1.1\r\n"
            f"Host: {host}\r\n"
            "Content-Type: multipart/form-data; boundary=x\r\n"
            f"Content-Length: {declared}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request)  # headers only; never sends the declared body
        sock.settimeout(10)
        data = sock.recv(4096)
    assert data.startswith(b"HTTP/1.1 413"), data[:60]


# --------------------------------------------------------------------------- #
# Routing
# --------------------------------------------------------------------------- #


def test_index_serves_upload_form(server):
    status, headers, body = _request(server, "GET", "/")
    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert b'action="/scan"' in body
    assert b'name="csrf_token"' in body
    assert b'accept=".csv,text/csv"' in body


def test_sample_csv_is_served_from_a_fixed_path(server):
    status, headers, body = _request(server, "GET", "/sample.csv")
    assert status == 200
    assert headers["content-type"].startswith("text/csv")
    assert body == SAMPLE.read_bytes()


@pytest.mark.parametrize(
    "path",
    ["/unknown", "/../etc/passwd", "/static/../pyproject.toml", "/scan.csv", "/%2e%2e/etc/passwd"],
)
def test_unknown_and_traversal_paths_are_not_served(server, path):
    status, _headers, _body = _request(server, "GET", path)
    assert status == 404


def test_post_to_unknown_route_is_not_found(server):
    status, _headers, _body = _request(
        server, "POST", "/nope", body=b"", content_type="multipart/form-data; boundary=x"
    )
    assert status == 404


# --------------------------------------------------------------------------- #
# CSRF and malformed requests
# --------------------------------------------------------------------------- #


def test_post_without_csrf_token_is_forbidden(server):
    status, _headers, _body = _post_csv(server, CLEAN_CSV, token="not-the-token")
    assert status == 403


def test_post_with_wrong_content_type_is_rejected(server):
    status, _headers, _body = _request(
        server, "POST", "/scan", body=b"business_id,name\n", content_type="application/json"
    )
    assert status == 400


def test_post_without_a_file_is_rejected(server):
    body, content_type = _multipart(
        {"action": "scan", "csrf_token": server.csrf_token}, {}
    )
    status, _headers, page = _request(
        server, "POST", "/scan", body=body, content_type=content_type
    )
    assert status == 400
    assert b"Choose a CSV file" in page


def test_non_csv_extension_is_rejected(server):
    status, _headers, page = _post_csv(server, CLEAN_CSV, filename="records.exe")
    assert status == 400
    assert b"Only .csv files" in page


# --------------------------------------------------------------------------- #
# End-to-end upload -> results
# --------------------------------------------------------------------------- #


def test_upload_produces_results(server):
    status, _headers, page = _post_csv(server, SAMPLE.read_bytes(), filename="sample_businesses.csv")
    assert status == 200
    assert b"Scan summary" in page
    assert b"Records analyzed" in page
    assert b"Duplicate candidates" in page
    assert b"Fuzzy thresholds" in page


def test_upload_states_duplicates_are_candidates_not_confirmed(server):
    _status, _headers, page = _post_csv(server, SAMPLE.read_bytes())
    assert b"candidates for human review" in page
    assert b"not confirmed duplicates" in page
    assert b"never merges or deletes" in page


def test_bundled_sample_action_scans_without_an_upload(server):
    body, content_type = _multipart(
        {"action": "sample", "csrf_token": server.csrf_token}, {}
    )
    status, _headers, page = _request(
        server, "POST", "/scan", body=body, content_type=content_type
    )
    assert status == 200
    assert b"sample_businesses.csv" in page
    assert b"17" in page


def test_clean_input_shows_empty_states(server):
    status, _headers, page = _post_csv(server, CLEAN_CSV)
    assert status == 200
    assert b"No findings were produced." in page
    assert b"No duplicate candidates were found." in page


# --------------------------------------------------------------------------- #
# Security: escaping
# --------------------------------------------------------------------------- #


def test_csv_derived_values_are_html_escaped(server):
    csv_bytes = b"business_id,name,city\n<script>alert(1)</script>,,\n"
    _status, _headers, page = _post_csv(server, csv_bytes)
    assert b"<script>alert(1)</script>" not in page
    assert b"&lt;script&gt;" in page


def test_uploaded_filename_is_escaped(server):
    status, _headers, page = _post_csv(
        server, CLEAN_CSV, filename="<img src=x onerror=alert(1)>.csv"
    )
    assert status == 200
    assert b"<img" not in page
    assert b"&lt;img" in page


# --------------------------------------------------------------------------- #
# Bind validation and the CLI subcommand
# --------------------------------------------------------------------------- #


def test_non_loopback_hosts_are_rejected():
    for host in ("0.0.0.0", "192.168.1.10", "example.com", "", "::"):
        with pytest.raises(InputError, match="loopback"):
            web.resolve_bind(host, 8000)


def test_loopback_hosts_are_accepted():
    assert web.resolve_bind("127.0.0.1", 8000) == ("127.0.0.1", 8000)
    assert web.resolve_bind("localhost", 8000) == ("localhost", 8000)
    assert web.resolve_bind("::1", 8000) == ("::1", 8000)


def test_invalid_port_is_rejected():
    with pytest.raises(InputError, match="port"):
        web.resolve_bind("127.0.0.1", 70000)


def test_web_command_appears_in_help():
    help_text = build_parser().format_help()
    assert "web" in help_text
    assert "analyze" in help_text


def test_cli_web_starts_and_stops_cleanly(monkeypatch):
    from localbiz_scout import cli

    calls: dict[str, bool] = {}

    class FakeServer:
        server_address = ("127.0.0.1", 8123)

        def serve_forever(self):
            calls["served"] = True

        def server_close(self):
            calls["closed"] = True

    monkeypatch.setattr(cli, "create_server", lambda host, port: FakeServer())
    return_code = cli.main(["web"])
    assert return_code == 0
    assert calls == {"served": True, "closed": True}


def test_cli_web_rejects_non_loopback_host():
    from localbiz_scout import cli

    assert cli.main(["web", "--host", "0.0.0.0"]) == 2


# --------------------------------------------------------------------------- #
# Phase 2.1 hardening: Host validation, framing, timeouts, ambiguity
# --------------------------------------------------------------------------- #


def test_host_header_validator():
    from localbiz_scout.web.server import host_header_ok

    for good in (
        "127.0.0.1",
        "127.0.0.1:8000",
        "localhost",
        "localhost:8000",
        "LOCALHOST",
        "LocalHost",
        "LocalHost:8000",
        "[::1]",
        "[::1]:8000",
    ):
        assert host_header_ok(good) is True, good

    for bad in (
        None,
        "",
        "example.com",
        "example.com:80",
        "localhost.evil.com",
        "0.0.0.0",
        "127.0.0.1@evil.com",
        "127.0.0.1:99999",
        "127.0.0.1:abc",
        "127.0.0.1:",
        ":8000",
        "a b",
        "foo/bar",
        "[::1",
        "[::2]:8000",
        "127.0.0.1\nX: y",
    ):
        assert host_header_ok(bad) is False, bad


@pytest.mark.parametrize(
    "host", ["127.0.0.1", "127.0.0.1:8000", "localhost", "localhost:8000", "[::1]", "[::1]:8000"]
)
def test_valid_loopback_host_header_is_accepted(server, host):
    request = f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii")
    data = _raw_exchange(server, request)
    assert data.startswith(b"HTTP/1.1 200"), data[:80]


@pytest.mark.parametrize(
    "host",
    ["example.com", "evil.example.com:8000", "0.0.0.0", "127.0.0.1@evil.com", "127.0.0.1:port", "a b"],
)
def test_invalid_host_header_is_rejected(server, host):
    request = f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii")
    data = _raw_exchange(server, request)
    assert data.startswith(b"HTTP/1.1 400"), data[:80]


def test_transfer_encoding_without_content_length_is_not_implemented(server):
    request = (
        "POST /scan HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: multipart/form-data; boundary=x\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    data = _raw_exchange(server, request)
    assert data.startswith(b"HTTP/1.1 501"), data[:80]


def test_transfer_encoding_with_content_length_is_rejected(server):
    request = (
        "POST /scan HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: multipart/form-data; boundary=x\r\n"
        "Transfer-Encoding: chunked\r\n"
        "Content-Length: 0\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    data = _raw_exchange(server, request)
    assert data.startswith(b"HTTP/1.1 400"), data[:80]


def test_stalled_upload_body_times_out(timeout_server):
    host, port = timeout_server.server_address[:2]
    request = (
        "POST /scan HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Content-Type: multipart/form-data; boundary=x\r\n"
        "Content-Length: 100000\r\n"
        "Connection: close\r\n\r\n"
        "partial-body"  # deliberately far short of the declared length
    ).encode("ascii")
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(request)
        sock.settimeout(10)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    assert data.startswith(b"HTTP/1.1 408"), data[:80]


def test_multipart_without_boundary_is_rejected(server):
    status, _headers, _body = _request(
        server, "POST", "/scan", body=b"not really multipart", content_type="multipart/form-data"
    )
    assert status == 400


def test_truncated_multipart_is_rejected_and_server_survives(server):
    body = (
        b'--x\r\nContent-Disposition: form-data; name="csrf_token"\r\n\r\n'
        + server.csrf_token.encode("utf-8")
        + b"\r\n"  # closing boundary omitted
    )
    status, _headers, _body = _request(
        server, "POST", "/scan", body=body, content_type="multipart/form-data; boundary=x"
    )
    assert 400 <= status < 500
    assert _request(server, "GET", "/")[0] == 200


def test_duplicate_csrf_field_is_rejected(server):
    body, content_type = _multipart_body(
        [
            ("csrf_token", server.csrf_token, None),
            ("csrf_token", server.csrf_token, None),
            ("action", "scan", None),
            ("csv", CLEAN_CSV, "records.csv"),
        ]
    )
    status, _headers, _body = _request(server, "POST", "/scan", body=body, content_type=content_type)
    assert status == 400


def test_duplicate_action_field_is_rejected(server):
    body, content_type = _multipart_body(
        [
            ("csrf_token", server.csrf_token, None),
            ("action", "sample", None),
            ("action", "scan", None),
            ("csv", CLEAN_CSV, "records.csv"),
        ]
    )
    status, _headers, _body = _request(server, "POST", "/scan", body=body, content_type=content_type)
    assert status == 400


def test_multiple_file_parts_are_rejected(server):
    body, content_type = _multipart_body(
        [
            ("csrf_token", server.csrf_token, None),
            ("action", "scan", None),
            ("csv", CLEAN_CSV, "a.csv"),
            ("csv", CLEAN_CSV, "b.csv"),
        ]
    )
    status, _headers, _body = _request(server, "POST", "/scan", body=body, content_type=content_type)
    assert status == 400


def test_oversized_file_part_is_rejected(server):
    oversized = b"a" * (web.MAX_UPLOAD_BYTES + 1)
    status, _headers, _body = _post_csv(server, oversized)
    assert status == 413


def test_cross_origin_post_is_forbidden(server):
    body, content_type = _multipart(
        {"action": "sample", "csrf_token": server.csrf_token}, {}
    )
    status, _headers, _body = _request(
        server,
        "POST",
        "/scan",
        body=body,
        content_type=content_type,
        headers={"Origin": "http://evil.example.com"},
    )
    assert status == 403


def test_null_origin_post_is_forbidden(server):
    body, content_type = _multipart(
        {"action": "sample", "csrf_token": server.csrf_token}, {}
    )
    status, _headers, _body = _request(
        server, "POST", "/scan", body=body, content_type=content_type, headers={"Origin": "null"}
    )
    assert status == 403


def test_loopback_origin_post_is_allowed(server):
    body, content_type = _multipart(
        {"action": "sample", "csrf_token": server.csrf_token}, {}
    )
    status, _headers, _body = _request(
        server,
        "POST",
        "/scan",
        body=body,
        content_type=content_type,
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    assert status == 200


@pytest.mark.parametrize("path", ["/", "/missing"])
def test_security_headers_are_present(server, path):
    _status, headers, _body = _request(server, "GET", path)
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer"
    assert headers["cache-control"] == "no-store"
    assert "default-src 'none'" in headers["content-security-policy"]


def test_ipv6_loopback_server_can_start():
    if not socket.has_ipv6:
        pytest.skip("IPv6 is not available on this host")
    try:
        srv = web.create_server("::1", 0, request_timeout=5)
    except OSError as error:
        pytest.skip(f"IPv6 loopback bind unavailable: {error}")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        status, _headers, _body = _request(srv, "GET", "/")
        assert status == 200
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# Phase 2.2 hardening: mixed-case Host, body deadline, exact media type,
# duplicate Host, timeout validation, server banner
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("host", ["LOCALHOST", "LocalHost", "LocalHost:8000", "LOCALHOST:8000"])
def test_mixed_case_localhost_host_header_is_accepted(server, host):
    request = f"GET / HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii")
    data = _raw_exchange(server, request)
    assert data.startswith(b"HTTP/1.1 200"), data[:80]


def test_duplicate_host_headers_are_rejected_before_routing(server):
    body, content_type = _multipart(
        {"action": "sample", "csrf_token": server.csrf_token}, {}
    )
    request = (
        "POST /scan HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Host: 127.0.0.1\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii") + body
    data = _raw_exchange(server, request)
    # A valid sample scan would be 200; a duplicate Host must fail first.
    assert data.startswith(b"HTTP/1.1 400"), data[:80]


def test_multipart_media_type_predicate():
    from localbiz_scout.web.server import _is_multipart_form_data

    assert _is_multipart_form_data("multipart/form-data") is True
    assert _is_multipart_form_data("MULTIPART/FORM-DATA") is True
    assert _is_multipart_form_data("Multipart/Form-Data; boundary=x") is True
    assert _is_multipart_form_data("multipart/form-data;boundary=x") is True
    assert _is_multipart_form_data("multipart/form-dataXYZ") is False
    assert _is_multipart_form_data("multipart/form-datax") is False
    assert _is_multipart_form_data("application/json") is False
    assert _is_multipart_form_data("") is False


def test_multipart_media_type_is_case_insensitive(server):
    body, _content_type = _multipart(
        {"action": "sample", "csrf_token": server.csrf_token}, {}
    )
    status, _headers, _body = _request(
        server,
        "POST",
        "/scan",
        body=body,
        content_type="Multipart/Form-Data; boundary=----localbizscoutboundary",
    )
    assert status == 200


def test_near_miss_media_type_is_rejected(server):
    body, content_type = _multipart(
        {"action": "sample", "csrf_token": server.csrf_token}, {}
    )
    bad_type = content_type.replace("multipart/form-data", "multipart/form-dataXYZ")
    status, _headers, _body = _request(
        server, "POST", "/scan", body=body, content_type=bad_type
    )
    assert status == 400


def test_invalid_request_timeouts_are_rejected():
    for value in (0, -1, -30, True, False):
        with pytest.raises(InputError, match="timeout"):
            web.create_server("127.0.0.1", 0, request_timeout=value)


def test_valid_request_timeout_is_applied():
    srv = web.create_server("127.0.0.1", 0, request_timeout=7)
    try:
        assert srv.request_timeout == 7
    finally:
        srv.server_close()


def test_server_header_hides_python_version(server):
    _status, headers, _body = _request(server, "GET", "/")
    assert headers["server"] == "LocalBizScout-web"
    assert "python" not in headers["server"].lower()


def test_slow_drip_body_read_hits_total_deadline(timeout_server):
    """A client that keeps dribbling bytes cannot extend past the total deadline.

    The idle timeout alone would be reset by every small chunk; the total
    body-read deadline (here 1s) must still fire well before the 30s default.
    """

    host, port = timeout_server.server_address[:2]
    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(
            (
                "POST /scan HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                "Content-Type: multipart/form-data; boundary=x\r\n"
                "Content-Length: 1000000\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        )
        sock.settimeout(0.2)
        start = time.monotonic()
        data = b""
        while time.monotonic() - start < 10 and b"\r\n\r\n" not in data:
            try:
                sock.sendall(b"a")  # drip; keeps the idle timeout from firing
            except OSError:
                pass
            try:
                chunk = sock.recv(4096)
            except (socket.timeout, TimeoutError):
                continue
            if not chunk:
                break
            data += chunk
        elapsed = time.monotonic() - start

    assert data.startswith(b"HTTP/1.1 408"), data[:80]
    assert elapsed < 6, f"server held the body read for {elapsed:.1f}s"
