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


def model_supports_verbose_json(model: str) -> bool:
    return "qwen3-asr" not in model.lower()


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
    want_timestamps: bool = timestamps != "none"
    use_verbose_json: bool = want_timestamps and model_supports_verbose_json(model)
    response_format: str = "verbose_json" if use_verbose_json else "json"
    data["response_format"] = response_format
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt
    if use_verbose_json and timestamps == "segment":
        data["timestamp_granularities[]"] = "segment"
    elif use_verbose_json and timestamps == "word":
        data["timestamp_granularities[]"] = "word"

    endpoint: str = url or f"{GROQ_BASE_URL}/audio/transcriptions"
    headers: dict[str, str] = {"Authorization": f"Bearer {api_key}"}
    if "openrouter.ai" in endpoint.lower():
        headers["HTTP-Referer"] = "https://github.com/kyoukisu/assistant-tools"
        headers["X-Title"] = "kit stt"
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
        if want_timestamps and not isinstance(parsed.get("segments"), list):
            seconds: float = 0.0
            usage: Any = parsed.get("usage")
            if isinstance(usage, dict) and usage.get("seconds") is not None:
                try:
                    seconds = float(usage["seconds"])
                except (TypeError, ValueError):
                    seconds = 0.0
            parsed["segments"] = [
                {
                    "id": 0,
                    "start": 0,
                    "end": seconds,
                    "text": str(parsed.get("text") or ""),
                }
            ]
            parsed["timestamps_note"] = (
                f"{model} does not support verbose_json; returned one clip-level segment. "
                "Use microsoft/mai-transcribe-2 when real segment/word timestamps are required."
            )
        return parsed
