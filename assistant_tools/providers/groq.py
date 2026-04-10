from __future__ import annotations

from pathlib import Path
from typing import Any

from assistant_tools.http import build_client
from assistant_tools.http import raise_for_error_response


GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"


def transcribe(
    *,
    api_key: str,
    source: str,
    timeout_seconds: float,
    model: str,
    language: str,
    timestamps: str,
    temperature: float,
    prompt: str,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "model": model,
        "temperature": str(temperature),
    }
    response_format: str = "verbose_json" if timestamps != "none" else "json"
    data["response_format"] = response_format
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt
    if timestamps == "segment":
        data["timestamp_granularities[]"] = "segment"
    elif timestamps == "word":
        data["timestamp_granularities[]"] = "word"

    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
    with build_client(timeout_seconds) as client:
        if source.startswith("http://") or source.startswith("https://"):
            data["url"] = source
            response = client.post(
                f"{GROQ_BASE_URL}/audio/transcriptions",
                headers=headers,
                data=data,
            )
        else:
            path: Path = Path(source).expanduser()
            with path.open("rb") as file_handle:
                response = client.post(
                    f"{GROQ_BASE_URL}/audio/transcriptions",
                    headers=headers,
                    data=data,
                    files={"file": (path.name, file_handle, "application/octet-stream")},
                )
        raise_for_error_response(response)
        parsed: dict[str, Any] = response.json()
        return parsed
