"""Minimal client for a local Ollama service (standard library only).

Only the local Ollama HTTP endpoint is ever contacted. No other network
access is performed. The client surfaces service failures as typed exceptions
so the adjudicator can degrade gracefully.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from typing import Protocol

DEFAULT_BASE_URL = "http://127.0.0.1:11434"


class AIError(Exception):
    """Base class for AI service errors."""


class AIUnavailableError(AIError):
    """The local service could not be reached."""


class AITimeoutError(AIError):
    """The local service did not respond within the timeout."""


class AIServiceError(AIError):
    """The service responded but the response was unusable."""


class AIClient(Protocol):
    """Protocol so tests can substitute a fake without a live service."""

    def chat(
        self,
        *,
        model: str,
        system: str,
        user: str,
        timeout: float,
        format_json: bool = True,
    ) -> str:  # pragma: no cover - protocol definition
        ...


class HttpOllamaClient:
    """Talk to a local Ollama server over HTTP."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def chat(
        self,
        *,
        model: str,
        system: str,
        user: str,
        timeout: float,
        format_json: bool = True,
    ) -> str:
        payload: dict[str, object] = {
            "model": model,
            "stream": False,
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if format_json:
            payload["format"] = "json"

        request = urllib.request.Request(
            url=f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except socket.timeout as error:
            raise AITimeoutError(f"request timed out after {timeout}s") from error
        except urllib.error.HTTPError as error:
            raise AIServiceError(f"service returned HTTP {error.code}") from error
        except urllib.error.URLError as error:
            reason = getattr(error, "reason", error)
            if isinstance(reason, socket.timeout):
                raise AITimeoutError(f"request timed out after {timeout}s") from error
            raise AIUnavailableError(f"service unreachable: {reason}") from error
        except OSError as error:
            raise AIUnavailableError(f"service unreachable: {error}") from error

        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as error:
            raise AIServiceError(f"service returned invalid JSON: {error}") from error

        message = parsed.get("message") if isinstance(parsed, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise AIServiceError("service response did not contain message content")

        return content
