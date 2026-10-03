from __future__ import annotations

import textwrap
from pathlib import Path

from assistant_tools.config import load_config


def test_load_config_ignores_retired_tts_keys(tmp_path: Path) -> None:
    config_path: Path = tmp_path / "config.toml"
    config_path.write_text(
        textwrap.dedent(
            """
            [tts]
            backend = "kittentts"
            clean_text = true
            voice = "Kore"
            """
        ).strip()
        + "\n"
    )

    config = load_config(config_path)
    assert config.tts.voice == "Kore"
    assert not hasattr(config.tts, "backend")
