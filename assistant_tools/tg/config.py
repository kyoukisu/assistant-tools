from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from assistant_tools.models import AppConfig
from assistant_tools.utils import AssistantToolsError
from assistant_tools.utils import require_env


@dataclass(slots=True)
class ResolvedTgConfig:
    api_id: int
    api_hash: str
    session_file: Path
    download_dir: Path
    cache_dir: Path
    session_string: str | None
    proxy: str | None
    takeout: bool
    sleep_threshold: int
    hide_password: bool


def _optional_env(name: str) -> str | None:
    try:
        return require_env(name)
    except AssistantToolsError:
        return None


def resolve_tg_config(app_config: AppConfig) -> ResolvedTgConfig:
    config = app_config.tg
    api_id_value: int = config.api_id
    if api_id_value == 0:
        env_api_id: str = require_env("TELEGRAM_API_ID")
        api_id_value = int(env_api_id)

    api_hash_value: str = config.api_hash or require_env("TELEGRAM_API_HASH")
    session_string_value: str | None = config.session_string or _optional_env(
        "TELEGRAM_SESSION_STRING"
    )

    session_file: Path = Path(config.session_file).expanduser()
    download_dir: Path = Path(config.download_dir).expanduser()
    cache_dir: Path = Path(config.cache_dir).expanduser()
    session_file.parent.mkdir(parents=True, exist_ok=True)
    download_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    return ResolvedTgConfig(
        api_id=api_id_value,
        api_hash=api_hash_value,
        session_file=session_file,
        download_dir=download_dir,
        cache_dir=cache_dir,
        session_string=session_string_value,
        proxy=config.proxy or None,
        takeout=config.takeout,
        sleep_threshold=config.sleep_threshold,
        hide_password=config.hide_password,
    )
