from __future__ import annotations

from datetime import UTC
from datetime import datetime
import importlib
from pathlib import Path
import tempfile
import re
import subprocess
from typing import Any

from assistant_tools.utils import AssistantToolsError


SAMPLE_RATE: int = 24000
CYRILLIC_RE: re.Pattern[str] = re.compile(r"[\u0400-\u04FF]")


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


def _ensure_english_text(text: str) -> None:
    if CYRILLIC_RE.search(text):
        raise AssistantToolsError(
            "KittenTTS currently supports English text only; Cyrillic/Russian input is not supported",
            error_type="unsupported_language",
            exit_code=2,
        )


def _load_dependencies() -> tuple[Any, Any]:
    try:
        kittentts_module: Any = importlib.import_module("kittentts")
        KittenTTS: Any = getattr(kittentts_module, "KittenTTS")
    except ImportError as err:
        raise AssistantToolsError(
            "Missing Python dependency: kittentts. Reinstall assistant-tools with the kitten-tts extra, for example: uv tool install 'assistant-tools[kitten-tts] @ git+https://github.com/kyoukisu/assistant-tools'",
            error_type="missing_dependency",
            exit_code=4,
        ) from err

    try:
        sf: Any = importlib.import_module("soundfile")
    except ImportError as err:
        raise AssistantToolsError(
            "Missing Python dependency: soundfile. Reinstall assistant-tools so runtime dependencies are present.",
            error_type="missing_dependency",
            exit_code=4,
        ) from err

    return KittenTTS, sf


def _load_model(KittenTTS: Any, model: str, backend: str) -> Any:
    try:
        return KittenTTS(model)
    except Exception as err:
        raise AssistantToolsError(
            f"Failed to load KittenTTS model '{model}': {err}",
            error_type="tts_model_error",
            exit_code=4,
        ) from err


def synthesize(
    *,
    text: str,
    model: str,
    voice: str,
    backend: str,
    speed: float,
    clean_text: bool,
    output: str | None,
    output_dir: str,
    save: bool,
    play: bool,
    volume: int,
) -> dict[str, Any]:
    _ensure_english_text(text)

    KittenTTS, sf = _load_dependencies()
    model_instance: Any = _load_model(KittenTTS, model, backend)

    try:
        audio: Any = model_instance.generate(text, voice=voice, speed=speed, clean_text=clean_text)
    except Exception as err:
        raise AssistantToolsError(
            f"KittenTTS synthesis failed: {err}",
            error_type="tts_generation_error",
            exit_code=4,
        ) from err

    should_save: bool = save or output is not None
    output_path: Path = (
        _resolve_output_path(output, output_dir) if should_save else _temporary_output_path()
    )
    try:
        sf.write(str(output_path), audio, SAMPLE_RATE)
    except Exception as err:
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

    sample_count: int = int(len(audio))
    duration_seconds: float = round(sample_count / SAMPLE_RATE, 4) if sample_count > 0 else 0.0
    return {
        "path": str(output_path) if persisted else None,
        "sample_rate": SAMPLE_RATE,
        "duration_seconds": duration_seconds,
        "voice": voice,
        "model": model,
        "backend": backend,
        "speed": speed,
        "clean_text": clean_text,
        "saved": persisted,
        "played": played,
        "volume": volume if played else None,
        "text_chars": len(text),
    }
