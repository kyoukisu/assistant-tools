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

def test_kitten_tts_extra_is_declared() -> None:
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads(Path("pyproject.toml").read_text())
    assert pyproject["project"]["optional-dependencies"]["kitten-tts"] == ["kittentts"]
    assert pyproject["tool"]["uv"]["sources"]["kittentts"]["url"].endswith(
        "kittentts-0.8.1-py3-none-any.whl"
    )