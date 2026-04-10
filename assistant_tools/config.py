from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import tomllib
from typing import Any

from assistant_tools.models import AppConfig
from assistant_tools.models import ExtractConfig
from assistant_tools.models import NetworkConfig
from assistant_tools.models import SearchConfig
from assistant_tools.models import SttConfig
from assistant_tools.models import VttConfig


DEFAULT_CONFIG_PATH: Path = Path("~/.config/assistant-tools/config.toml").expanduser()


def _section(data: dict[str, Any], key: str) -> dict[str, Any]:
    section_value: Any = data.get(key, {})
    if not isinstance(section_value, dict):
        raise ValueError(f"Config section '{key}' must be a table/object")
    result: dict[str, Any] = {}
    for section_key, section_item in section_value.items():
        result[str(section_key)] = section_item
    return result


def load_config(config_path: Path | None = None) -> AppConfig:
    resolved_path: Path = (config_path or DEFAULT_CONFIG_PATH).expanduser()
    raw: dict[str, Any] = {}
    if resolved_path.exists():
        raw = tomllib.loads(resolved_path.read_text())

    network_config: NetworkConfig = NetworkConfig(**_section(raw, "network"))
    stt_config: SttConfig = SttConfig(**_section(raw, "stt"))
    search_config: SearchConfig = SearchConfig(**_section(raw, "search"))
    extract_config: ExtractConfig = ExtractConfig(**_section(raw, "extract"))
    vtt_config: VttConfig = VttConfig(**_section(raw, "vtt"))

    return AppConfig(
        network=network_config,
        stt=stt_config,
        search=search_config,
        extract=extract_config,
        vtt=vtt_config,
    )


def config_snapshot(config: AppConfig, config_path: Path | None = None) -> dict[str, Any]:
    resolved_path: Path = (config_path or DEFAULT_CONFIG_PATH).expanduser()
    return {
        "config_path": str(resolved_path),
        "config_exists": resolved_path.exists(),
        "config": asdict(config),
    }
