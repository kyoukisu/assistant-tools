from __future__ import annotations

from typing import Any

import httpx

from assistant_tools.utils import AssistantToolsError


def build_client(timeout_seconds: float) -> httpx.Client:
    return httpx.Client(timeout=timeout_seconds, follow_redirects=True)


def raise_for_error_response(response: httpx.Response) -> None:
    if response.is_success:
        return

    message: str = response.text
    error_type: str = "http_error"
    try:
        payload: Any = response.json()
        if isinstance(payload, dict):
            if "error" in payload and isinstance(payload["error"], dict):
                error_payload: dict[str, Any] = {}
                for error_key, error_value in payload["error"].items():
                    error_payload[str(error_key)] = error_value
                message = str(error_payload.get("message", message))
                error_type = str(error_payload.get("type", error_type))
            elif "message" in payload:
                payload_message: Any = payload.get("message")
                payload_error: Any = payload.get("error", error_type)
                message = str(payload_message)
                error_type = str(payload_error)
    except Exception:
        pass

    raise AssistantToolsError(
        f"HTTP {response.status_code}: {message}",
        error_type=error_type,
        exit_code=1,
    )
