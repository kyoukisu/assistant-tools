from __future__ import annotations

from assistant_tools import __version__
from assistant_tools.cli import build_parser


def test_parser_exposes_version_flag() -> None:
    parser = build_parser()
    action_flags = {flag for action in parser._actions for flag in action.option_strings}
    assert "--version" in action_flags



def test_package_version_fallback_is_semverish() -> None:
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(part.isdigit() for part in parts)


def test_parser_exposes_video_command() -> None:
    parser = build_parser()
    subparsers_actions = [action for action in parser._actions if hasattr(action, "choices")]
    commands = set()
    for action in subparsers_actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            commands.update(choices.keys())
    assert "video" in commands


def test_video_parser_exposes_explicit_timestamp_flag() -> None:
    parser = build_parser()
    subparsers_actions = [action for action in parser._actions if hasattr(action, "choices")]
    video_parser = None
    for action in subparsers_actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and "video" in choices:
            video_parser = choices["video"]
            break
    assert video_parser is not None
    action_flags = {flag for action in video_parser._actions for flag in action.option_strings}
    assert "--at" in action_flags



def test_kitten_tts_extra_is_declared() -> None:
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["optional-dependencies"]["kitten-tts"] == ["kittentts"]
    source = pyproject["tool"]["uv"]["sources"]["kittentts"]
    assert source["git"] == "https://github.com/KittenML/KittenTTS"
    assert source["rev"] == "395171a68d5c73a50027436988fb856c30c748b8"
