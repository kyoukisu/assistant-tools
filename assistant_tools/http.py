from __future__ import annotations

from typing import Any
from typing import cast

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
            typed_payload: dict[object, Any] = cast(dict[object, Any], payload)
            if "error" in payload and isinstance(payload["error"], dict):
                raw_error_payload: dict[object, Any] = cast(dict[object, Any], payload["error"])
                error_payload: dict[str, Any] = {}
                for error_key, error_value in raw_error_payload.items():
                    error_payload[str(error_key)] = error_value
                message = str(error_payload.get("message", message))
                error_type = str(error_payload.get("type", error_type))
            elif "message" in payload:
                payload_message: Any = typed_payload.get("message")
                payload_error: Any = typed_payload.get("error", error_type)
                message = str(payload_message)
                error_type = str(payload_error)
    except Exception:
        pass

    raise AssistantToolsError(
        f"HTTP {response.status_code}: {message}",
        error_type=error_type,
        exit_code=1,
    )
