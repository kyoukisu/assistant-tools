from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class NetworkConfig:
    timeout_seconds: float = 60.0


@dataclass(slots=True)
class SttConfig:
    model: str = "whisper-large-v3"
    language: str = ""
    timestamps: str = "none"
    temperature: float = 0.0
    prompt: str = ""


@dataclass(slots=True)
class SearchConfig:
    mode: str = "agentic"
    max_results: int = 5
    max_chars_per_result: int = 4000
    max_chars_total: int = 12000


@dataclass(slots=True)
class ExtractConfig:
    full_content: bool = False
    max_chars_per_result: int = 5000


@dataclass(slots=True)
class VttConfig:
    mode: str = "auto"
    text: bool = True
    lang: str = ""
    wait: bool = True
    poll_interval_seconds: float = 1.0
    wait_timeout_seconds: float = 180.0


@dataclass(slots=True)
class TgConfig:
    api_id: int = 0
    api_hash: str = ""
    session_file: str = "~/.local/state/assistant-tools/tg/main.session"
    download_dir: str = "~/.local/state/assistant-tools/tg/downloads"
    cache_dir: str = "~/.local/state/assistant-tools/tg/cache"
    session_string: str = ""
    proxy: str = ""
    takeout: bool = False
    sleep_threshold: int = 10
    hide_password: bool = False


@dataclass(slots=True)
class AppConfig:
    network: NetworkConfig
    stt: SttConfig
    search: SearchConfig
    extract: ExtractConfig
    vtt: VttConfig
    tg: TgConfig


@dataclass(slots=True)
class CommandResult:
    ok: bool
    command: str
    provider: str
    data: dict[str, Any] | None
    error: dict[str, Any] | None
    meta: dict[str, Any]
