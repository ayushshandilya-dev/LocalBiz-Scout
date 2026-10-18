"""Tests for the local Ollama client (no live service required)."""

import json
import socket
import urllib.error
import urllib.request

import pytest

from localbiz_scout.ai.client import (
    AIServiceError,
    AITimeoutError,
    AIUnavailableError,
    HttpOllamaClient,
)


class FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_request_construction(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        captured["timeout"] = timeout
        body = json.dumps({"message": {"content": '{"ok": true}'}}).encode()
        return FakeResponse(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    client = HttpOllamaClient(base_url="http://127.0.0.1:9999/")
    content = client.chat(
        model="gemma3:1b", system="SYS", user="USER", timeout=7
    )

    request = captured["request"]
    assert request.full_url == "http://127.0.0.1:9999/api/chat"
    assert request.get_method() == "POST"
    assert captured["timeout"] == 7

    payload = json.loads(request.data.decode("utf-8"))
    assert payload["model"] == "gemma3:1b"
    assert payload["stream"] is False
    assert payload["format"] == "json"
    assert payload["messages"][0] == {"role": "system", "content": "SYS"}
    assert payload["messages"][1] == {"role": "user", "content": "USER"}

    assert content == '{"ok": true}'


def test_timeout_from_socket(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise socket.timeout("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = HttpOllamaClient()
    with pytest.raises(AITimeoutError):
        client.chat(model="m", system="s", user="u", timeout=1)


def test_timeout_wrapped_in_urlerror(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = HttpOllamaClient()
    with pytest.raises(AITimeoutError):
        client.chat(model="m", system="s", user="u", timeout=1)


def test_connection_refused_is_unavailable(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError(61, "refused"))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = HttpOllamaClient()
    with pytest.raises(AIUnavailableError):
        client.chat(model="m", system="s", user="u", timeout=1)


def test_http_error_is_service_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    client = HttpOllamaClient()
    with pytest.raises(AIServiceError):
        client.chat(model="m", system="s", user="u", timeout=1)


def test_invalid_json_body_is_service_error(monkeypatch):
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None: FakeResponse(b"not json")
    )
    client = HttpOllamaClient()
    with pytest.raises(AIServiceError):
        client.chat(model="m", system="s", user="u", timeout=1)


def test_missing_message_content_is_service_error(monkeypatch):
    body = json.dumps({"message": {}}).encode()
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda request, timeout=None: FakeResponse(body)
    )
    client = HttpOllamaClient()
    with pytest.raises(AIServiceError):
        client.chat(model="m", system="s", user="u", timeout=1)
