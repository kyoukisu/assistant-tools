from __future__ import annotations

from datetime import UTC
from datetime import datetime
from pathlib import Path
import tempfile
import subprocess
import time
import wave
from typing import Any

from assistant_tools.http import build_client
from assistant_tools.http import raise_for_error_response
from assistant_tools.utils import AssistantToolsError
from assistant_tools.utils import require_stt_api_key


DEFAULT_TTS_URL: str = "https://openrouter.ai/api/v1/audio/speech"
DEFAULT_TTS_MODEL: str = "google/gemini-3.8-flash-lite-tts"
DEFAULT_TTS_VOICE: str = "Kore"
LOCAL_VOICES: set[str] = {
    "f1",
    "f2",
    "f3",
    "f4",
    "f5",
    "m1",
    "m2",
    "m3",
    "m4",
    "m5",
    "rosie",
    "kiki",
    "luna",
    "bella",
}
LOCAL_MODELS: set[str] = {
    "",
    "supertonic",
    "supertonic-3",
    "supertonic3",
    "kitten",
    "kittentts",
}


def _resolve_output_path(output: str | None, output_dir: str) -> Path:
    if output:
        resolved_output: Path = Path(output).expanduser()
        resolved_output.parent.mkdir(parents=True, exist_ok=True)
        return resolved_output

    timestamp: str = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    directory: Path = Path(output_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"tts-{timestamp}.wav"


def _temporary_output_path() -> Path:
    with tempfile.NamedTemporaryFile(
        prefix="assistant-tools-tts-", suffix=".wav", delete=False
    ) as tmp:
        return Path(tmp.name)


def _resolve_model(model: str) -> str:
    value: str = model.strip()
    lowered: str = value.lower()
    if lowered in LOCAL_MODELS or "kitten" in lowered:
        return DEFAULT_TTS_MODEL
    return value


def _resolve_voice(voice: str) -> str:
    value: str = voice.strip()
    if not value or value.lower() in LOCAL_VOICES:
        return DEFAULT_TTS_VOICE
    return value


def _pcm_layout(content_type: str) -> tuple[int, int]:
    rate: int = 24000
    channels: int = 1
    for part in content_type.split(";"):
        key, separator, raw = part.strip().partition("=")
        if not separator or not raw.isdigit():
            continue
        if key == "rate":
            rate = int(raw)
        elif key == "channels":
            channels = int(raw)
    return rate, channels


def _looks_like_mp3(audio: bytes, content_type: str) -> bool:
    lowered: str = content_type.lower()
    if "mpeg" in lowered or "mp3" in lowered:
        return True
    return audio.startswith(b"ID3") or audio[:2] in {b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"}


def _write_pcm_wav(audio: bytes, content_type: str, output_path: Path) -> tuple[int, float]:
    rate, channels = _pcm_layout(content_type)
    sample_width: int = 2
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(rate)
        wav_file.writeframes(audio)
    frame_width: int = sample_width * channels
    frames: int = len(audio) // frame_width if frame_width else 0
    duration: float = frames / rate if rate else 0.0
    return rate, duration


def _write_mp3_wav(audio: bytes, output_path: Path) -> tuple[int, float]:
    with tempfile.NamedTemporaryFile(
        prefix="assistant-tools-tts-", suffix=".mp3", delete=False
    ) as tmp:
        tmp.write(audio)
        mp3_path: Path = Path(tmp.name)
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(mp3_path), "-ac", "1", str(output_path)],
            check=True,
            capture_output=True,
        )
    except FileNotFoundError as err:
        raise AssistantToolsError(
            "OpenRouter returned MP3 and ffmpeg is not available to wrap it as WAV",
            error_type="missing_runtime",
            exit_code=5,
        ) from err
    except subprocess.CalledProcessError as err:
        detail: str = (err.stderr or b"").decode("utf-8", "replace").strip()
        suffix: str = f": {detail}" if detail else ""
        raise AssistantToolsError(
            f"ffmpeg failed to wrap OpenRouter audio{suffix}",
            error_type="tts_write_error",
            exit_code=5,
        ) from err
    finally:
        mp3_path.unlink(missing_ok=True)
    return _wav_stats(output_path)


def _wav_stats(path: Path) -> tuple[int, float]:
    with wave.open(str(path), "rb") as wav_file:
        rate: int = wav_file.getframerate() or 0
        frames: int = wav_file.getnframes()
    duration: float = frames / rate if rate else 0.0
    return rate, duration


def _write_audio(audio: bytes, content_type: str, output_path: Path) -> tuple[int, float]:
    if not audio:
        raise AssistantToolsError(
            "OpenRouter TTS returned an empty audio stream",
            error_type="tts_generation_error",
            exit_code=4,
        )
    if audio.startswith(b"RIFF"):
        output_path.write_bytes(audio)
        return _wav_stats(output_path)
    if _looks_like_mp3(audio, content_type):
        return _write_mp3_wav(audio, output_path)
    return _write_pcm_wav(audio, content_type, output_path)


def synthesize(
    *,
    text: str,
    model: str,
    voice: str,
    speed: float,
    clean_text: bool,
    output: str | None,
    output_dir: str,
    save: bool,
    play: bool,
    volume: int,
    backend: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    del clean_text, backend
    selected_model: str = _resolve_model(model)
    selected_voice: str = _resolve_voice(voice)
    should_save: bool = save or output is not None
    output_path: Path = (
        _resolve_output_path(output, output_dir) if should_save else _temporary_output_path()
    )
    api_key: str = require_stt_api_key("")
    body: dict[str, Any] = {
        "model": selected_model,
        "input": text,
        "voice": selected_voice,
        "response_format": "pcm",
    }

    started: float = time.perf_counter()
    try:
        with build_client(120.0, None) as client:
            response = client.post(
                DEFAULT_TTS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            raise_for_error_response(response)
            audio: bytes = response.content
            content_type: str = response.headers.get("content-type", "")
    except AssistantToolsError:
        raise
    except Exception as err:
        raise AssistantToolsError(
            f"OpenRouter TTS failed: {err}",
            error_type="tts_generation_error",
            exit_code=4,
        ) from err
    generation_seconds: float = time.perf_counter() - started

    try:
        sample_rate, duration_seconds = _write_audio(audio, content_type, output_path)
    except AssistantToolsError:
        output_path.unlink(missing_ok=True)
        raise
    except Exception as err:
        output_path.unlink(missing_ok=True)
        raise AssistantToolsError(
            f"Failed to write WAV file: {output_path}",
            error_type="tts_write_error",
            exit_code=5,
        ) from err

    played: bool = False
    if play:
        command: list[str] = ["paplay", "--volume", str(volume), str(output_path)]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError as err:
            raise AssistantToolsError(
                "paplay is not available in PATH",
                error_type="missing_runtime",
                exit_code=5,
            ) from err
        except subprocess.CalledProcessError as err:
            stderr: str = (err.stderr or "").strip()
            detail: str = f": {stderr}" if stderr else ""
            raise AssistantToolsError(
                f"paplay failed{detail}",
                error_type="tts_playback_error",
                exit_code=5,
            ) from err
        played = True

    persisted: bool = should_save
    if not persisted:
        try:
            output_path.unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "path": str(output_path) if persisted else None,
        "backend": "openrouter",
        "sample_rate": sample_rate,
        "duration_seconds": round(duration_seconds, 4),
        "generation_seconds": round(generation_seconds, 4),
        "rtf": round(generation_seconds / duration_seconds, 4) if duration_seconds > 0 else 0.0,
        "model": selected_model,
        "voice": selected_voice,
        "language": language or None,
        "speed": speed,
        "clean_text": None,
        "saved": persisted,
        "played": played,
        "volume": volume if played else None,
        "text_chars": len(text),
    }
