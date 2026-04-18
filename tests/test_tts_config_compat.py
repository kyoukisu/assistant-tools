from __future__ import annotations

import textwrap
from pathlib import Path

from assistant_tools.config import load_config


def test_load_config_ignores_legacy_tts_backend_field(tmp_path: Path) -> None:
    config_path: Path = tmp_path / "config.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [tts]
            backend = "cpu"
            voice = "Kiki"
            """
        ).strip()
        + "\n"
    )

    config = load_config(config_path)
    assert config.tts.voice == "Kiki"
