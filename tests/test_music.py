from __future__ import annotations

import asyncio
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import wave

import pytest

from assistant_tools import music
from assistant_tools.cli import build_parser
from assistant_tools.utils import AssistantToolsError


def test_parser():
    args = build_parser().parse_args(
        ["music", "recognize", "clip.mp4", "--start", "2", "--duration", "8"]
    )
    assert (args.music_command, args.start, args.duration, args.timeout) == ("recognize", 2, 8, 60)
    args = build_parser().parse_args(
        ["music", "recognize", "--start", "0", "--duration", "15", "--", "-clip.wav"]
    )
    assert args.input == "-clip.wav"


def test_cli_import_does_not_load_shazamio():
    import subprocess

    result = subprocess.run(
        [sys.executable, "-c", "import assistant_tools.cli, sys; print('shazamio' in sys.modules)"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "False"


def test_conversion_timeout_cleanup(tmp_path, monkeypatch):
    import subprocess

    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    observed = []
    monkeypatch.setattr(music, "require_binary", lambda name: name)

    def timeout(command, **kwargs):
        observed.append(Path(command[-1]))
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(music.subprocess, "run", timeout)
    with pytest.raises(AssistantToolsError, match="timed out"):
        music.recognize_local(str(source))
    assert not observed[0].parent.exists()


def test_normalized_results():
    assert music.normalize_result({"matches": []}) == {"matched": False, "track": None}
    assert music.normalize_result(
        {"track": {"title": "Song", "subtitle": "Artist", "key": "1", "url": "url", "huge": []}}
    ) == {"matched": True, "track": {"title": "Song", "artist": "Artist", "key": "1", "url": "url"}}


@pytest.mark.parametrize(
    "options",
    [
        {"start": -1},
        {"start": float("nan")},
        {"duration": 0},
        {"duration": 31},
        {"duration": float("inf")},
        {"timeout": 0},
    ],
)
def test_invalid_bounds(options):
    with pytest.raises(AssistantToolsError):
        music.recognize_local("missing.wav", **options)


def test_missing_local_file():
    with pytest.raises(AssistantToolsError, match="local file"):
        music.recognize_local("https://example.com/file.mp3")


def test_lazy_dependency(monkeypatch):
    def missing(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(music.importlib, "import_module", missing)
    with pytest.raises(AssistantToolsError, match=r"assistant-tools\[music\]"):
        asyncio.run(music._recognize(Path("sample.wav"), 1))


def test_recognition_timeout(monkeypatch):
    class Shazam:
        async def recognize(self, path):
            await asyncio.Event().wait()

    monkeypatch.setitem(sys.modules, "shazamio", SimpleNamespace(Shazam=Shazam))
    with pytest.raises(TimeoutError):
        asyncio.run(music._recognize(Path("sample.wav"), 0.01))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg unavailable")
@pytest.mark.parametrize("outcome", ["match", "none", "timeout", "failure"])
def test_real_conversion_and_cleanup(tmp_path, monkeypatch, outcome):
    source = tmp_path / "source.wav"
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(b"\0" * 8000 * 4 * 3)
    observed = []

    class Shazam:
        async def recognize(self, path):
            observed.append(Path(path))
            with wave.open(path) as audio:
                assert audio.getframerate() == 16000
                assert audio.getnchannels() == 1
                assert audio.getnframes() == 16000
            if outcome == "timeout":
                raise TimeoutError
            if outcome == "failure":
                raise RuntimeError("service failed")
            return {"track": {"title": "Song"}} if outcome == "match" else {"matches": []}

    monkeypatch.setitem(sys.modules, "shazamio", SimpleNamespace(Shazam=Shazam))
    if outcome in {"timeout", "failure"}:
        with pytest.raises(AssistantToolsError):
            music.recognize_local(str(source), start=1, duration=1)
    else:
        assert music.recognize_local(str(source), start=1, duration=1)["matched"] == (
            outcome == "match"
        )
    assert observed and not observed[0].parent.exists()


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg unavailable")
def test_invalid_media(tmp_path):
    source = tmp_path / "bad.wav"
    source.write_text("not media")
    with pytest.raises(AssistantToolsError, match="extract audio"):
        music.recognize_local(str(source))
