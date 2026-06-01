from __future__ import annotations

from pathlib import Path
from typing import Any

from assistant_tools.http import build_client
from assistant_tools.http import raise_for_error_response


GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
GROQ_AUDIO_EXTENSION_ALIASES: dict[str, str] = {".oga": ".ogg"}
GROQ_AUDIO_MIME_TYPES: dict[str, str] = {
    ".flac": "audio/flac",
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
}


def upload_file_metadata(path: Path) -> tuple[str, str]:
    suffix: str = path.suffix.lower()
    upload_suffix: str = GROQ_AUDIO_EXTENSION_ALIASES.get(suffix, suffix)
    upload_name: str = f"{path.stem}{upload_suffix}" if upload_suffix != suffix else path.name
    content_type: str = GROQ_AUDIO_MIME_TYPES.get(upload_suffix, "application/octet-stream")
    return upload_name, content_type


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
    proxy: str | None,
    url: str | None = None,
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

    endpoint: str = url or f"{GROQ_BASE_URL}/audio/transcriptions"
    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
    with build_client(timeout_seconds, proxy) as client:
        if source.startswith("http://") or source.startswith("https://"):
            data["url"] = source
            response = client.post(
                endpoint,
                headers=headers,
                data=data,
            )
        else:
            path: Path = Path(source).expanduser()
            upload_name: str
            content_type: str
            upload_name, content_type = upload_file_metadata(path)
            with path.open("rb") as file_handle:
                response = client.post(
                    endpoint,
                    headers=headers,
                    data=data,
                    files={"file": (upload_name, file_handle, content_type)},
                )
        raise_for_error_response(response)
        parsed: dict[str, Any] = response.json()
        return parsed
