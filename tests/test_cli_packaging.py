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
