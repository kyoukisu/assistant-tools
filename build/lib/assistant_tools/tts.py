from __future__ import annotations

from contextlib import redirect_stderr
from contextlib import redirect_stdout
from datetime import UTC
from datetime import datetime
import importlib
import io
from pathlib import Path
import tempfile
import re
import subprocess
import time
from typing import Any

from assistant_tools.utils import AssistantToolsError


KITTEN_SAMPLE_RATE: int = 24000
SUPERTONIC_SAMPLE_RATE: int = 44100
CYRILLIC_RE: re.Pattern[str] = re.compile(r"[\u0400-\u04FF]")
DEFAULT_KITTEN_MODEL: str = "KittenML/kitten-tts-micro-0.8"
DEFAULT_SUPERTONIC_MODEL: str = "supertonic-3"


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
            "KittenTTS supports English text only; use Supertonic for Cyrillic/Russian input",
            error_type="unsupported_language",
            exit_code=2,
        )


def _auto_language(text: str, language: str | None) -> str | None:
    if language:
        return language
    if CYRILLIC_RE.search(text):
        return "ru"
    return "en"


def _silence_upstream(call: Any) -> Any:
    sink = io.StringIO()
    with redirect_stdout(sink), redirect_stderr(sink):
        return call()


def _load_kittentts_dependencies() -> tuple[Any, Any]:
    try:
        kittentts_module: Any = importlib.import_module("kittentts")
        KittenTTS: Any = getattr(kittentts_module, "KittenTTS")
    except ImportError as err:
        raise AssistantToolsError(
            "Missing Python dependency: kittentts. Reinstall assistant-tools with KittenTTS support.",
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


def _load_kittentts_model(KittenTTS: Any, model: str) -> Any:
    try:
        return _silence_upstream(lambda: KittenTTS(model))
    except Exception as err:
        raise AssistantToolsError(
            f"Failed to load KittenTTS model '{model}': {err}",
            error_type="tts_model_error",
            exit_code=4,
        ) from err


def _load_supertonic_dependencies() -> Any:
    try:
        module: Any = importlib.import_module("supertonic")
        return getattr(module, "TTS")
    except ImportError as err:
        raise AssistantToolsError(
            "Missing Python dependency: supertonic. Reinstall assistant-tools with Supertonic support.",
            error_type="missing_dependency",
            exit_code=4,
        ) from err


def _synthesize_kittentts(
    *,
    text: str,
    model: str,
    voice: str,
    speed: float,
    clean_text: bool,
    output_path: Path,
) -> dict[str, Any]:
    _ensure_english_text(text)
    KittenTTS, sf = _load_kittentts_dependencies()
    model_name = DEFAULT_KITTEN_MODEL if model in {"", "kitten", "kittentts"} else model
    model_instance: Any = _load_kittentts_model(KittenTTS, model_name)

    started = time.perf_counter()
    try:
        audio: Any = _silence_upstream(
            lambda: model_instance.generate(
                text, voice=voice, speed=speed, clean_text=clean_text
            )
        )
    except Exception as err:
        raise AssistantToolsError(
            f"KittenTTS synthesis failed: {err}",
            error_type="tts_generation_error",
            exit_code=4,
        ) from err
    generation_seconds = time.perf_counter() - started

    try:
        sf.write(str(output_path), audio, KITTEN_SAMPLE_RATE)
    except Exception as err:
        raise AssistantToolsError(
            f"Failed to write WAV file: {output_path}",
            error_type="tts_write_error",
            exit_code=5,
        ) from err

    sample_count: int = int(len(audio))
    duration_seconds: float = sample_count / KITTEN_SAMPLE_RATE if sample_count > 0 else 0.0
    return {
        "sample_rate": KITTEN_SAMPLE_RATE,
        "duration_seconds": round(duration_seconds, 4),
        "generation_seconds": round(generation_seconds, 4),
        "rtf": round(generation_seconds / duration_seconds, 4) if duration_seconds > 0 else 0.0,
        "model": model_name,
        "voice": voice,
        "language": "en",
    }


def _synthesize_supertonic(
    *,
    text: str,
    model: str,
    voice: str,
    speed: float,
    language: str | None,
    output_path: Path,
) -> dict[str, Any]:
    TTS: Any = _load_supertonic_dependencies()
    model_name = DEFAULT_SUPERTONIC_MODEL if model in {"", "supertonic"} else model
    lang = _auto_language(text, language)

    try:
        tts = _silence_upstream(lambda: TTS(model=model_name, auto_download=True))
        style = tts.get_voice_style(voice)
    except Exception as err:
        raise AssistantToolsError(
            f"Failed to load Supertonic model '{model_name}' voice '{voice}': {err}",
            error_type="tts_model_error",
            exit_code=4,
        ) from err

    started = time.perf_counter()
    try:
        wav, duration = _silence_upstream(
            lambda: tts.synthesize(
                text,
                voice_style=style,
                lang=lang,
                speed=speed,
                total_steps=8,
                verbose=False,
            )
        )
    except Exception as err:
        raise AssistantToolsError(
            f"Supertonic synthesis failed: {err}",
            error_type="tts_generation_error",
            exit_code=4,
        ) from err
    generation_seconds = time.perf_counter() - started

    try:
        tts.save_audio(wav, str(output_path))
    except Exception as err:
        raise AssistantToolsError(
            f"Failed to write WAV file: {output_path}",
            error_type="tts_write_error",
            exit_code=5,
        ) from err

    try:
        duration_seconds = float(duration[-1]) if hasattr(duration, "__len__") else float(duration)
    except Exception:
        duration_seconds = 0.0

    return {
        "sample_rate": SUPERTONIC_SAMPLE_RATE,
        "duration_seconds": round(duration_seconds, 4),
        "generation_seconds": round(generation_seconds, 4),
        "rtf": round(generation_seconds / duration_seconds, 4) if duration_seconds > 0 else 0.0,
        "model": model_name,
        "voice": voice,
        "language": lang,
    }


def _normalize_backend(backend: str | None, model: str) -> str:
    value = (backend or "").strip().lower()
    if value in {"kitten", "kittentts"}:
        return "kittentts"
    if value in {"supertonic", "supertonic3", "supertonic-3"}:
        return "supertonic"
    model_lower = model.lower()
    if model_lower in {"kitten", "kittentts"} or "kitten" in model_lower:
        return "kittentts"
    return "supertonic"


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
    should_save: bool = save or output is not None
    output_path: Path = (
        _resolve_output_path(output, output_dir) if should_save else _temporary_output_path()
    )

    selected_backend = _normalize_backend(backend, model)
    if selected_backend == "kittentts":
        payload = _synthesize_kittentts(
            text=text,
            model=model,
            voice=voice,
            speed=speed,
            clean_text=clean_text,
            output_path=output_path,
        )
    else:
        payload = _synthesize_supertonic(
            text=text,
            model=model,
            voice=voice,
            speed=speed,
            language=language,
            output_path=output_path,
        )

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
        "backend": selected_backend,
        **payload,
        "speed": speed,
        "clean_text": clean_text if selected_backend == "kittentts" else None,
        "saved": persisted,
        "played": played,
        "volume": volume if played else None,
        "text_chars": len(text),
    }
