from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from assistant_tools.models import AppConfig
from assistant_tools.models import TgProfileConfig
from assistant_tools.utils import AssistantToolsError
from assistant_tools.utils import require_env


@dataclass(slots=True)
class ResolvedTgConfig:
    profile: str
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


def _coalesce_str(*values: str | None) -> str:
    for value in values:
        if value:
            return value
    return ""


def _resolve_profile_paths(
    *,
    profile_name: str,
    default_profile: str,
    base_session_file: str,
    session_dir: str,
    base_download_dir: str,
    base_cache_dir: str,
    profile_config: TgProfileConfig | None,
) -> tuple[Path, Path, Path]:
    if profile_config is not None and profile_config.session_file:
        session_file: Path = Path(profile_config.session_file).expanduser()
    elif profile_name == default_profile:
        session_file = Path(base_session_file).expanduser()
    else:
        session_file = Path(session_dir).expanduser() / f"{profile_name}.session"

    if profile_config is not None and profile_config.download_dir:
        download_dir: Path = Path(profile_config.download_dir).expanduser()
    elif profile_name == default_profile:
        download_dir = Path(base_download_dir).expanduser()
    else:
        download_dir = Path(base_download_dir).expanduser() / profile_name

    if profile_config is not None and profile_config.cache_dir:
        cache_dir: Path = Path(profile_config.cache_dir).expanduser()
    elif profile_name == default_profile:
        cache_dir = Path(base_cache_dir).expanduser()
    else:
        cache_dir = Path(base_cache_dir).expanduser() / profile_name

    return session_file, download_dir, cache_dir


def resolve_tg_config(
    app_config: AppConfig, profile_override: str | None = None
) -> ResolvedTgConfig:
    config = app_config.tg
    profile_name: str = profile_override or config.default_profile or "main"
    profile_config: TgProfileConfig | None = config.profiles.get(profile_name)

    api_id_value: int = (
        profile_config.api_id
        if profile_config is not None and profile_config.api_id != 0
        else config.api_id
    )
    if api_id_value == 0:
        env_api_id: str = require_env("TELEGRAM_API_ID")
        api_id_value = int(env_api_id)

    api_hash_value: str = _coalesce_str(
        profile_config.api_hash if profile_config is not None else "",
        config.api_hash,
        _optional_env("TELEGRAM_API_HASH"),
    )
    if not api_hash_value:
        api_hash_value = require_env("TELEGRAM_API_HASH")

    session_string_value: str | None = (
        _coalesce_str(
            profile_config.session_string if profile_config is not None else "",
            config.session_string,
            _optional_env("TELEGRAM_SESSION_STRING"),
        )
        or None
    )

    session_file, download_dir, cache_dir = _resolve_profile_paths(
        profile_name=profile_name,
        default_profile=config.default_profile,
        base_session_file=config.session_file,
        session_dir=config.session_dir,
        base_download_dir=config.download_dir,
        base_cache_dir=config.cache_dir,
        profile_config=profile_config,
    )

    session_file.parent.mkdir(parents=True, exist_ok=True)
    download_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)

    return ResolvedTgConfig(
        profile=profile_name,
        api_id=api_id_value,
        api_hash=api_hash_value,
        session_file=session_file,
        download_dir=download_dir,
        cache_dir=cache_dir,
        session_string=session_string_value,
        proxy=_coalesce_str(
            profile_config.proxy if profile_config is not None else "",
            config.proxy,
        )
        or None,
        takeout=config.takeout,
        sleep_threshold=config.sleep_threshold,
        hide_password=config.hide_password,
    )
