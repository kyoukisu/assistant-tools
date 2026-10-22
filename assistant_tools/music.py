"""Bounded local-media recognition; ShazamIO is imported only on demand."""

from __future__ import annotations

import asyncio
import importlib
import math
from pathlib import Path
import subprocess
import tempfile
from typing import Any, cast

from assistant_tools.utils import AssistantToolsError
from assistant_tools.video import require_binary


def normalize_result(raw: dict[str, Any]) -> dict[str, Any]:
    track = raw.get("track")
    if not isinstance(track, dict):
        return {"matched": False, "track": None}
    track = cast(dict[str, Any], track)
    if not track.get("title"):
        return {"matched": False, "track": None}
    return {
        "matched": True,
        "track": {
            "title": track.get("title"),
            "artist": track.get("subtitle"),
            "key": track.get("key"),
            "url": track.get("url"),
        },
    }


async def _recognize(path: Path, timeout: float) -> dict[str, Any]:
    try:
        module = importlib.import_module("shazamio")
    except ImportError as exc:
        raise AssistantToolsError(
            "Music recognition requires assistant-tools[music] (including audioop-lts on Python 3.13+)",
            error_type="missing_dependency",
            exit_code=2,
        ) from exc
    return await asyncio.wait_for(module.Shazam().recognize(str(path)), timeout=timeout)


def recognize_local(
    source: str, *, start: float = 0, duration: float = 15, timeout: float = 60
) -> dict[str, Any]:
    if not math.isfinite(start) or start < 0:
        raise AssistantToolsError(
            "start must be finite and nonnegative", error_type="invalid_input", exit_code=2
        )
    if not math.isfinite(duration) or not 0 < duration <= 30:
        raise AssistantToolsError(
            "duration must be between 0 (exclusive) and 30 seconds",
            error_type="invalid_input",
            exit_code=2,
        )
    if not math.isfinite(timeout) or timeout <= 0:
        raise AssistantToolsError(
            "timeout must be finite and positive", error_type="invalid_input", exit_code=2
        )
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise AssistantToolsError(
            "input must be an existing local file", error_type="invalid_input", exit_code=2
        )
    ffmpeg = require_binary("ffmpeg")
    with tempfile.TemporaryDirectory(prefix="kit-music-") as directory:
        audio = Path(directory) / "sample.wav"
        try:
            converted = subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    str(start),
                    "-protocol_whitelist",
                    "file,pipe",
                    "-i",
                    str(source_path),
                    "-t",
                    str(duration),
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(audio),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if converted.returncode or not audio.exists() or audio.stat().st_size <= 78:
                raise AssistantToolsError(
                    "ffmpeg could not extract audio from the requested window",
                    error_type="conversion_failed",
                    exit_code=2,
                )
            raw = asyncio.run(_recognize(audio, timeout))
        except (subprocess.TimeoutExpired, TimeoutError) as exc:
            raise AssistantToolsError(
                "Music recognition timed out", error_type="timeout", exit_code=2
            ) from exc
        except AssistantToolsError:
            raise
        except Exception as exc:
            raise AssistantToolsError(
                "Shazam recognition failed", error_type="recognition_failed", exit_code=2
            ) from exc
    return normalize_result(raw)
