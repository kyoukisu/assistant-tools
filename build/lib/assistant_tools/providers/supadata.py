from __future__ import annotations

from typing import Any
import time

from assistant_tools.http import build_client
from assistant_tools.http import raise_for_error_response
from assistant_tools.utils import AssistantToolsError


SUPADATA_BASE_URL: str = "https://api.supadata.ai/v1"


def request_transcript(
    *,
    api_key: str,
    url: str,
    timeout_seconds: float,
    mode: str,
    lang: str,
    text: bool,
    proxy: str | None,
) -> tuple[int, dict[str, Any]]:
    params: dict[str, Any] = {"url": url, "mode": mode, "text": str(text).lower()}
    if lang:
        params["lang"] = lang

    with build_client(timeout_seconds, proxy) as client:
        response = client.get(
            f"{SUPADATA_BASE_URL}/transcript",
            headers={"x-api-key": api_key},
            params=params,
        )
        if response.status_code not in (200, 202):
            raise_for_error_response(response)
        parsed: dict[str, Any] = response.json()
        return response.status_code, parsed


def poll_transcript_job(
    *,
    api_key: str,
    job_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
    wait_timeout_seconds: float,
    proxy: str | None,
) -> dict[str, Any]:
    deadline: float = time.monotonic() + wait_timeout_seconds
    with build_client(timeout_seconds, proxy) as client:
        while True:
            response = client.get(
                f"{SUPADATA_BASE_URL}/transcript/{job_id}",
                headers={"x-api-key": api_key},
            )
            raise_for_error_response(response)
            payload: dict[str, Any] = response.json()
            status: str = str(payload.get("status", "unknown"))
            if status in {"completed", "failed"}:
                return payload
            if time.monotonic() >= deadline:
                raise AssistantToolsError(
                    f"Supadata transcript job timed out: {job_id}",
                    error_type="job_timeout",
                    exit_code=1,
                )
            time.sleep(poll_interval_seconds)
