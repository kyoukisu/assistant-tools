from __future__ import annotations

import argparse
import asyncio as _asyncio
import json
from pathlib import Path
from typing import Any

from assistant_tools import __version__
from assistant_tools.config import config_snapshot
from assistant_tools.config import load_config
from assistant_tools.models import AppConfig
from assistant_tools.models import CommandResult
from assistant_tools.providers import groq as groq_provider
from assistant_tools.providers import parallel as parallel_provider
from assistant_tools.providers import supadata as supadata_provider
from assistant_tools import shardx as shardx_provider
from assistant_tools import tts as tts_provider
from assistant_tools import video as video_provider
from assistant_tools.tg.config import resolve_tg_config
from assistant_tools.tg import commands as tg_commands
from assistant_tools.utils import AssistantToolsError
from assistant_tools.utils import emit_result
from assistant_tools.utils import ensure_path_exists
from assistant_tools.utils import error_result
from assistant_tools.utils import is_url
from assistant_tools.utils import require_env


DAEMON_LOCKED_ERROR = "database is locked"


def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(prog="kit")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, default=None, help="Path to config TOML file")
    parser.add_argument("--verbose", action="store_true", help="Include config snapshot in meta")

    subparsers = parser.add_subparsers(dest="command", required=True)

    stt_parser = subparsers.add_parser("stt", help="Speech to text via Groq")
    stt_parser.add_argument("input", help="Audio file path or URL")
    stt_parser.add_argument("--language", default=None, help="Language code override")
    stt_parser.add_argument(
        "--timestamps",
        choices=["none", "segment", "word"],
        default=None,
        help="Timestamp granularity",
    )
    stt_parser.add_argument("--model", default=None, help="Groq model override")
    stt_parser.add_argument("--prompt", default=None, help="Optional spelling/context prompt")

    search_parser = subparsers.add_parser("search", help="Web search via Parallel")
    search_parser.add_argument("query", help="Search query/objective")
    search_parser.add_argument("--mode", choices=["fast", "one-shot", "agentic"], default=None)
    search_parser.add_argument("--max-results", type=int, default=None)
    search_parser.add_argument("--after-date", default=None, help="Filter after YYYY-MM-DD")
    search_parser.add_argument(
        "--domain",
        action="append",
        default=None,
        help="Include domain filter, repeatable",
    )

    extract_parser = subparsers.add_parser("extract", help="URL extract via Parallel")
    extract_parser.add_argument("url", nargs="+", help="One or more URLs to extract")
    extract_parser.add_argument("--objective", default=None, help="Optional extraction objective")
    extract_parser.add_argument("--full-content", action="store_true", help="Return full content")

    vtt_parser = subparsers.add_parser("vtt", help="Video to text via Supadata")
    vtt_parser.add_argument("input", help="Video URL")
    vtt_parser.add_argument("--lang", default=None, help="Preferred language code")
    vtt_parser.add_argument(
        "--mode",
        choices=["native", "auto", "generate"],
        default=None,
        help="Supadata transcript mode",
    )
    vtt_parser.add_argument(
        "--wait",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Wait for async transcript job completion",
    )
    vtt_parser.add_argument(
        "--chunks",
        action="store_true",
        help="Return timestamped chunks instead of plain text",
    )

    video_parser = subparsers.add_parser(
        "video",
        help="Extract evenly spread frames and optional audio transcript from a local video",
    )
    video_parser.add_argument("input", help="Local video or GIF path")
    video_parser.add_argument(
        "--output-dir", type=Path, default=None, help="Run output directory root"
    )
    video_parser.add_argument(
        "--max-frames", type=int, default=None, help="Maximum frames to extract"
    )
    video_parser.add_argument(
        "--seconds-per-frame",
        type=float,
        default=None,
        help="Target spacing budget before max frame cap applies",
    )
    video_parser.add_argument(
        "--frame-format",
        choices=["jpg", "png", "webp"],
        default=None,
        help="Image format for extracted frames",
    )
    video_parser.add_argument(
        "--align-to-segments",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Nudge frame timestamps toward speech segment midpoints when available",
    )
    video_parser.add_argument(
        "--transcribe",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Extract audio and run Groq STT when audio exists",
    )
    video_parser.add_argument(
        "--timestamps",
        choices=["none", "segment", "word"],
        default=None,
        help="Timestamp granularity for the transcript payload",
    )
    video_parser.add_argument(
        "--at",
        dest="at_seconds",
        action="append",
        type=float,
        default=None,
        help="Extract frame(s) at explicit second offsets. Repeatable. When provided, overrides auto spreading.",
    )
    video_parser.add_argument("--language", default=None, help="Language code override")
    video_parser.add_argument("--model", default=None, help="Groq model override")
    video_parser.add_argument("--prompt", default=None, help="Optional spelling/context prompt")

    tts_parser = subparsers.add_parser(
        "tts", help="Local text to speech via Supertonic or KittenTTS"
    )
    tts_parser.add_argument("text", help="Text to synthesize")
    tts_parser.add_argument(
        "--backend",
        choices=["supertonic", "kittentts", "kitten"],
        default=None,
        help="TTS backend override",
    )
    tts_parser.add_argument("--voice", default=None, help="Voice name override")
    tts_parser.add_argument("--model", default=None, help="TTS model override")
    tts_parser.add_argument(
        "--language", default=None, help="Language code override, e.g. en or ru"
    )
    tts_parser.add_argument("--speed", type=float, default=None, help="Speech speed")
    tts_parser.add_argument(
        "--clean-text",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Normalize text automatically before synthesis",
    )
    tts_parser.add_argument(
        "--save",
        action="store_true",
        help="Persist generated WAV to disk. Without this, default mode plays audio without keeping a file.",
    )
    tts_parser.add_argument("--output", type=Path, default=None, help="Output WAV path override")
    tts_parser.add_argument(
        "--play",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Play generated WAV via paplay",
    )
    tts_parser.add_argument(
        "--volume",
        type=int,
        default=None,
        help="paplay volume for --play (PulseAudio scale, e.g. 45000)",
    )

    shardx_parser = subparsers.add_parser("shardx", help="ShardX browser control client")
    shardx_subparsers = shardx_parser.add_subparsers(dest="shardx_command", required=True)
    shardx_subparsers.add_parser("health", help="Check ShardX API health")
    shardx_subparsers.add_parser("sessions", help="List active ShardX sessions")
    shardx_subparsers.add_parser("identities", help="List stored ShardX identities")

    shardx_identity = shardx_subparsers.add_parser("identity", help="Manage ShardX identities")
    shardx_identity_subparsers = shardx_identity.add_subparsers(dest="identity_command", required=True)
    shardx_identity_create = shardx_identity_subparsers.add_parser("create", help="Create an identity")
    shardx_identity_create.add_argument("name")
    shardx_identity_create.add_argument("--proxy")
    shardx_identity_create.add_argument("--fingerprint")
    shardx_identity_open = shardx_identity_subparsers.add_parser("open", help="Open a URL with an identity")
    shardx_identity_open.add_argument("name")
    shardx_identity_open.add_argument("url")
    shardx_identity_open.add_argument("--live", action="store_true")
    shardx_identity_open_last = shardx_identity_subparsers.add_parser(
        "open-last", help="Reopen an identity's last URL"
    )
    shardx_identity_open_last.add_argument("name")
    shardx_identity_status = shardx_identity_subparsers.add_parser("status", help="Show identity status")
    shardx_identity_status.add_argument("name")
    shardx_identity_capture = shardx_identity_subparsers.add_parser(
        "capture-cookies", help="Capture identity cookies"
    )
    shardx_identity_capture.add_argument("name")

    shardx_open = shardx_subparsers.add_parser("open", help="Open a URL")
    shardx_open.add_argument("url")
    shardx_open_mode = shardx_open.add_mutually_exclusive_group()
    shardx_open_mode.add_argument("--identity")
    shardx_open_mode.add_argument("--session")
    shardx_open.add_argument("--proxy")
    shardx_open.add_argument("--fingerprint")
    shardx_open.add_argument("--live", action="store_true")

    def add_shardx_session(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--session", required=True)

    shardx_observe = shardx_subparsers.add_parser("observe", help="Observe a session")
    add_shardx_session(shardx_observe)
    shardx_read = shardx_subparsers.add_parser("read", help="Read sanitized page content")
    add_shardx_session(shardx_read)
    shardx_read.add_argument("--full-page", action="store_true")
    shardx_read.add_argument("--region", choices=["auto", "main", "page"], default="auto")
    shardx_read.add_argument("--max-chars", type=int, default=6000)
    shardx_read.add_argument("--max-blocks", type=int, default=40)
    shardx_read_ref = shardx_subparsers.add_parser(
        "read-ref", help="Read sanitized content scoped to a current control ref"
    )
    add_shardx_session(shardx_read_ref)
    shardx_read_ref.add_argument("snapshot")
    shardx_read_ref.add_argument("ref")
    shardx_read_ref.add_argument("--max-chars", type=int, default=2000)
    shardx_read_ref.add_argument("--max-blocks", type=int, default=20)

    shardx_act = shardx_subparsers.add_parser("act", help="Act on an opaque control ref")
    add_shardx_session(shardx_act)
    shardx_act.add_argument("snapshot")
    shardx_act.add_argument("ref")
    shardx_act.add_argument("action", choices=["click", "fill", "type"])
    shardx_act.add_argument("--text")
    shardx_act.add_argument("--append", action="store_true")
    shardx_secret_fill = shardx_subparsers.add_parser(
        "secret-fill", help="Fill a browser control from an environment secret"
    )
    add_shardx_session(shardx_secret_fill)
    shardx_secret_fill.add_argument("snapshot")
    shardx_secret_fill.add_argument("ref")
    shardx_secret_fill.add_argument(
        "--env-var", default="ANYXPAY_PASSWORD", help="Environment variable containing the secret"
    )

    shardx_page = shardx_subparsers.add_parser("page", help="Perform a page-level action")
    add_shardx_session(shardx_page)
    shardx_page.add_argument("action", choices=["scroll", "press", "back", "reload", "wait"])
    shardx_page.add_argument("--direction", choices=["up", "down"], default="down")
    shardx_page.add_argument("--pages", type=float, default=1.0)
    shardx_page.add_argument("--key")
    shardx_page.add_argument("--seconds", type=float, default=3.0)

    shardx_tabs = shardx_subparsers.add_parser("tabs", help="List or select tabs")
    add_shardx_session(shardx_tabs)
    shardx_tabs.add_argument("--select")
    shardx_close_tab = shardx_subparsers.add_parser("close-tab", help="Close a tab")
    add_shardx_session(shardx_close_tab)
    shardx_close_tab.add_argument("tab")
    shardx_stop = shardx_subparsers.add_parser("stop", help="Stop a session")
    add_shardx_session(shardx_stop)
    shardx_screenshot = shardx_subparsers.add_parser("screenshot", help="Save a PNG screenshot")
    add_shardx_session(shardx_screenshot)
    shardx_screenshot.add_argument("--output", type=Path, required=True)
    shardx_screenshot.add_argument(
        "--annotate", metavar="SNAPSHOT", default=None,
        help="Overlay opaque control refs from this observation snapshot",
    )

    config_parser = subparsers.add_parser("config", help="Show or edit kit configuration")
    config_subparsers = config_parser.add_subparsers(dest="config_command")
    config_subparsers.add_parser("show", help="Show current config")
    config_set = config_subparsers.add_parser("set", help="Set a config value")
    config_set.add_argument("key", help="Config key (e.g. stt.api_key)")
    config_set.add_argument("value", help="Value to set")
    config_subparsers.add_parser("path", help="Show config file path")

    tg_parser = subparsers.add_parser("tg", help="Telegram CLI via Telethon")
    tg_parser.add_argument(
        "--profile",
        default=None,
        help="Telegram profile name. Defaults to tg.default_profile",
    )
    tg_subparsers = tg_parser.add_subparsers(dest="tg_command", required=True)

    tg_auth_parser = tg_subparsers.add_parser("auth", help="Telegram auth commands")
    tg_auth_subparsers = tg_auth_parser.add_subparsers(dest="tg_auth_command", required=True)
    tg_auth_login = tg_auth_subparsers.add_parser("login", help="Interactive Telegram login")
    tg_auth_login.add_argument("--phone", default=None, help="Phone number override")
    tg_auth_subparsers.add_parser("status", help="Show Telegram auth status")
    tg_auth_subparsers.add_parser("logout", help="Delete local Telegram session file")
    tg_auth_subparsers.add_parser("export-session", help="Export current session string")
    tg_auth_import = tg_auth_subparsers.add_parser("import-session", help="Import session string")
    tg_auth_import.add_argument("session_string", help="Session string value")

    tg_resolve = tg_subparsers.add_parser("resolve", help="Resolve a Telegram peer")
    tg_resolve.add_argument("peer", help="Username, id, me, or t.me link")

    tg_miniapp = tg_subparsers.add_parser(
        "miniapp", help="Get a Telegram-issued Mini App launch URL"
    )
    tg_miniapp_subparsers = tg_miniapp.add_subparsers(dest="miniapp_command", required=True)
    tg_miniapp_main = tg_miniapp_subparsers.add_parser(
        "main", help="Get a launch URL for a bot's configured Main Mini App"
    )
    tg_miniapp_main.add_argument("bot", help="Bot username or id")
    tg_miniapp_main.add_argument(
        "--start-param", default=None, help="Optional Main Mini App start parameter"
    )
    tg_miniapp_main.add_argument(
        "--platform", choices=["tdesktop", "android", "ios"], default="tdesktop"
    )
    tg_miniapp_mode = tg_miniapp_main.add_mutually_exclusive_group()
    tg_miniapp_mode.add_argument("--compact", action="store_true", help="Request compact mode")
    tg_miniapp_mode.add_argument(
        "--fullscreen", action="store_true", help="Request fullscreen mode"
    )
    tg_miniapp_menu = tg_miniapp_subparsers.add_parser(
        "menu", help="Get a launch URL from a bot's Mini App menu button"
    )
    tg_miniapp_menu.add_argument("bot", help="Bot username or id")
    tg_miniapp_menu.add_argument(
        "--start-param", default=None, help="Optional Mini App start parameter"
    )
    tg_miniapp_menu.add_argument(
        "--platform", choices=["tdesktop", "android", "ios"], default="tdesktop"
    )
    tg_miniapp_menu_mode = tg_miniapp_menu.add_mutually_exclusive_group()
    tg_miniapp_menu_mode.add_argument("--compact", action="store_true", help="Request compact mode")
    tg_miniapp_menu_mode.add_argument(
        "--fullscreen", action="store_true", help="Request fullscreen mode"
    )
    tg_miniapp_open = tg_miniapp_subparsers.add_parser(
        "open",
        help="Fetch a fresh Mini App URL and open it in ShardX with a persistent identity",
    )
    tg_miniapp_open.add_argument("bot", help="Bot username or id")
    tg_miniapp_open.add_argument(
        "--identity", required=True, help="ShardX identity name to open the app with"
    )
    tg_miniapp_open.add_argument(
        "--source", choices=["menu", "main"], default="menu", help="Which Mini App entry to use"
    )
    tg_miniapp_open.add_argument(
        "--start-param", default=None, help="Optional Mini App start parameter"
    )
    tg_miniapp_open.add_argument(
        "--platform", choices=["tdesktop", "android", "ios"], default="tdesktop"
    )
    tg_miniapp_open_mode = tg_miniapp_open.add_mutually_exclusive_group()
    tg_miniapp_open_mode.add_argument("--compact", action="store_true", help="Request compact mode")
    tg_miniapp_open_mode.add_argument(
        "--fullscreen", action="store_true", help="Request fullscreen mode"
    )
    tg_miniapp_open.add_argument(
        "--wait-ms", type=int, default=2500, help="Milliseconds to wait before observing the app"
    )
    tg_miniapp_open.add_argument("--live", action="store_true", help="Open noVNC live view")


    tg_dialogs = tg_subparsers.add_parser("dialogs", help="List Telegram dialogs")
    tg_dialogs.add_argument("--limit", type=int, default=20)
    tg_dialogs.add_argument("--full", action="store_true", help="Return fuller dialog objects")

    tg_participants = tg_subparsers.add_parser("participants", help="List chat participants")
    tg_participants.add_argument("peer", help="Target peer")
    tg_participants.add_argument("--limit", type=int, default=200)

    tg_history = tg_subparsers.add_parser("history", help="Read chat history")
    tg_history.add_argument("peer", help="Peer id, username, or me")
    tg_history.add_argument("--limit", type=int, default=20)
    tg_history.add_argument("--offset-id", type=int, default=0)
    tg_history.add_argument("--full", action="store_true", help="Return fuller message objects")

    tg_get = tg_subparsers.add_parser("get", help="Get message(s) from a chat")
    tg_get.add_argument("peer", help="Peer id or username")
    tg_get.add_argument("message_ids", nargs="+", type=int, help="One or more message ids")
    tg_get.add_argument("--full", action="store_true", help="Return fuller message objects")

    tg_send = tg_subparsers.add_parser("send", help="Send text message")
    tg_send.add_argument("peer", help="Target peer")
    tg_send.add_argument("text", help="Message text")
    tg_send.add_argument("--reply-to", type=int, default=None, help="Reply target message id")
    tg_send.add_argument(
        "--parse-mode", default=None, choices=["md", "html"], help="Parse mode: md or html"
    )
    tg_send.add_argument("--full", action="store_true", help="Return fuller sent message object")

    tg_send_file = tg_subparsers.add_parser("send-file", help="Send local file as document")
    tg_send_file.add_argument("peer", help="Target peer")
    tg_send_file.add_argument("path", help="Local file path")
    tg_send_file.add_argument("--caption", default=None, help="Optional caption")
    tg_send_file.add_argument("--reply-to", type=int, default=None, help="Reply target message id")
    tg_send_file.add_argument(
        "--full", action="store_true", help="Return fuller sent message object"
    )

    tg_send_media = tg_subparsers.add_parser(
        "send-media",
        aliases=["send-photo"],
        help="Send local media as Telegram photo/video (multiple = album)",
    )
    tg_send_media.add_argument("peer", help="Target peer")
    tg_send_media.add_argument("path", nargs="+", help="Local image/video path(s)")
    tg_send_media.add_argument("--caption", default=None, help="Optional caption")
    tg_send_media.add_argument("--reply-to", type=int, default=None, help="Reply target message id")
    tg_send_media.add_argument(
        "--full", action="store_true", help="Return fuller sent message object"
    )
    tg_send_media.add_argument(
        "--as-gif", action="store_true", help="Send video as GIF/animation (no sound, autoplay)"
    )

    tg_send_voice = tg_subparsers.add_parser(
        "send-voice", help="Send local audio file as Telegram voice note"
    )
    tg_send_voice.add_argument("peer", help="Target peer")
    tg_send_voice.add_argument("path", help="Local audio file path")
    tg_send_voice.add_argument("--caption", default=None, help="Optional caption")
    tg_send_voice.add_argument("--reply-to", type=int, default=None, help="Reply target message id")
    tg_send_voice.add_argument(
        "--full", action="store_true", help="Return fuller sent message object"
    )

    tg_speak = tg_subparsers.add_parser(
        "speak", help="Synthesize English speech and send it as a Telegram voice note"
    )
    tg_speak.add_argument("peer", help="Target peer")
    tg_speak.add_argument("text", help="English text to synthesize and send")
    tg_speak.add_argument("--caption", default=None, help="Optional caption")
    tg_speak.add_argument("--reply-to", type=int, default=None, help="Reply target message id")
    tg_speak.add_argument("--backend", choices=["supertonic", "kittentts", "kitten"], default=None)
    tg_speak.add_argument("--voice", default=None, help="Voice name override")
    tg_speak.add_argument("--model", default=None, help="TTS model override")
    tg_speak.add_argument("--language", default=None, help="Language code override, e.g. en or ru")
    tg_speak.add_argument("--speed", type=float, default=None, help="Speech speed")
    tg_speak.add_argument(
        "--clean-text",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Normalize text automatically before synthesis",
    )
    tg_speak.add_argument("--full", action="store_true", help="Return fuller sent message object")

    tg_react = tg_subparsers.add_parser("react", help="React to a message")
    tg_react.add_argument("peer", help="Target peer")
    tg_react.add_argument("message_id", type=int, help="Target message id")
    tg_react.add_argument("emoji", help="Reaction emoji")

    tg_search = tg_subparsers.add_parser("search", help="Search messages in a chat")
    tg_search.add_argument("peer", help="Target peer")
    tg_search.add_argument("query", help="Search query")
    tg_search.add_argument("--limit", type=int, default=20)
    tg_search.add_argument("--full", action="store_true", help="Return fuller message objects")

    tg_wait_next = tg_subparsers.add_parser("wait-next", help="Wait for the next incoming message")
    tg_wait_next.add_argument("peer", nargs="+", help="Target peer(s) — one or more")
    tg_wait_next.add_argument(
        "--timeout",
        "--timeout-seconds",
        type=float,
        default=0,
        help="Seconds to wait (0 = infinite)",
    )
    tg_wait_next.add_argument("--full", action="store_true", help="Return fuller message object")

    tg_media_info = tg_subparsers.add_parser("media-info", help="Show media metadata")
    tg_media_info.add_argument("peer", help="Target peer")
    tg_media_info.add_argument("message_id", type=int, help="Message id")
    tg_media_info.add_argument("--full", action="store_true", help="Include full message object")

    tg_media_download = tg_subparsers.add_parser("media-download", help="Download message media")
    tg_media_download.add_argument("peer", help="Target peer")
    tg_media_download.add_argument("message_ids", nargs="+", type=int, help="Message ids")
    tg_media_download.add_argument("--output-dir", default=None, help="Output directory override")
    tg_media_download.add_argument(
        "--full", action="store_true", help="Include full message object"
    )

    tg_copy = tg_subparsers.add_parser("copy", help="Copy message to another chat")
    tg_copy.add_argument("source_peer", help="Source peer")
    tg_copy.add_argument("message_id", type=int, help="Source message id")
    tg_copy.add_argument("target_peer", help="Target peer")
    tg_copy.add_argument("--full", action="store_true", help="Return fuller copied message object")

    tg_forward = tg_subparsers.add_parser("forward", help="Forward message(s) to another chat")
    tg_forward.add_argument("from_peer", help="Source peer")
    tg_forward.add_argument("to_peer", help="Destination peer")
    tg_forward.add_argument("message_ids", nargs="+", type=int, help="Message IDs to forward")

    tg_edit = tg_subparsers.add_parser("edit", help="Edit a message")
    tg_edit.add_argument("peer", help="Target peer")
    tg_edit.add_argument("message_id", type=int, help="Message ID to edit")
    tg_edit.add_argument("text", help="New text")
    tg_edit.add_argument("--parse-mode", default=None, choices=["md", "html"], help="Parse mode")

    tg_delete = tg_subparsers.add_parser("delete", help="Delete message(s)")
    tg_delete.add_argument("peer", help="Target peer")
    tg_delete.add_argument("message_ids", nargs="+", type=int, help="Message IDs to delete")

    tg_stt = tg_subparsers.add_parser("stt", help="Download voice/audio message and transcribe")
    tg_stt.add_argument("peer", help="Target peer")
    tg_stt.add_argument("message_id", type=int, help="Message id with voice/audio")
    tg_stt.add_argument("--language", default="", help="Language hint (e.g. ru, en)")

    tg_find_dialog = tg_subparsers.add_parser(
        "find-dialog", help="Search Telegram globally for unverified peer candidates"
    )
    tg_find_dialog.add_argument("query", help="Search query (name, username, title)")
    tg_find_dialog.add_argument("--limit", type=int, default=20, help="Max results to return")

    # Hidden internal command for daemon process
    tg_subparsers.add_parser("_daemon", help=argparse.SUPPRESS)

    return parser


def _meta(
    command: str, config: AppConfig, config_path: Path | None, verbose: bool
) -> dict[str, Any]:
    base: dict[str, Any] = {"command": command}
    if verbose:
        base["config"] = config_snapshot(config, config_path)
    return base


def run_stt(
    args: argparse.Namespace, config: AppConfig, verbose: bool, config_path: Path | None
) -> CommandResult:
    source: str = str(args.input)
    if not is_url(source):
        ensure_path_exists(source)
    api_key: str = config.stt.api_key or require_env("GROQ_API_KEY")
    model: str = args.model or config.stt.model
    language: str = args.language if args.language is not None else config.stt.language
    timestamps: str = args.timestamps if args.timestamps is not None else config.stt.timestamps
    prompt: str = args.prompt if args.prompt is not None else config.stt.prompt

    payload: dict[str, Any] = groq_provider.transcribe(
        api_key=api_key,
        source=source,
        timeout_seconds=config.network.timeout_seconds,
        model=model,
        language=language,
        timestamps=timestamps,
        temperature=config.stt.temperature,
        prompt=prompt,
        proxy=config.network.proxy or None,
        url=config.stt.url or None,
    )
    return CommandResult(
        ok=True,
        command="stt",
        provider="groq",
        data=payload,
        error=None,
        meta={
            **_meta("stt", config, config_path, verbose),
            "input": source,
            "model": model,
            "language": language,
            "timestamps": timestamps,
        },
    )


def run_search(
    args: argparse.Namespace,
    config: AppConfig,
    verbose: bool,
    config_path: Path | None,
) -> CommandResult:
    api_key: str = require_env("PARALLEL_API_KEY")
    mode: str = args.mode or config.search.mode
    max_results: int = args.max_results or config.search.max_results
    include_domains: list[str] = args.domain or []

    payload: dict[str, Any] = parallel_provider.search(
        api_key=api_key,
        objective=args.query,
        timeout_seconds=config.network.timeout_seconds,
        mode=mode,
        max_results=max_results,
        after_date=args.after_date,
        include_domains=include_domains,
        max_chars_per_result=config.search.max_chars_per_result,
        max_chars_total=config.search.max_chars_total,
        proxy=config.network.proxy or None,
    )
    return CommandResult(
        ok=True,
        command="search",
        provider="parallel",
        data=payload,
        error=None,
        meta={
            **_meta("search", config, config_path, verbose),
            "query": args.query,
            "mode": mode,
            "max_results": max_results,
            "domains": include_domains,
            "after_date": args.after_date,
        },
    )


def run_extract(
    args: argparse.Namespace,
    config: AppConfig,
    verbose: bool,
    config_path: Path | None,
) -> CommandResult:
    api_key: str = require_env("PARALLEL_API_KEY")
    urls: list[str] = [str(item) for item in args.url]

    payload: dict[str, Any] = parallel_provider.extract(
        api_key=api_key,
        urls=urls,
        objective=args.objective,
        timeout_seconds=config.network.timeout_seconds,
        full_content=bool(args.full_content or config.extract.full_content),
        max_chars_per_result=config.extract.max_chars_per_result,
        proxy=config.network.proxy or None,
    )
    return CommandResult(
        ok=True,
        command="extract",
        provider="parallel",
        data=payload,
        error=None,
        meta={
            **_meta("extract", config, config_path, verbose),
            "urls": urls,
            "objective": args.objective,
            "full_content": bool(args.full_content or config.extract.full_content),
        },
    )


def run_vtt(
    args: argparse.Namespace, config: AppConfig, verbose: bool, config_path: Path | None
) -> CommandResult:
    api_key: str = require_env("SUPADATA_API_KEY")
    mode: str = args.mode or config.vtt.mode
    lang: str = args.lang if args.lang is not None else config.vtt.lang
    text: bool = not bool(args.chunks) if args.chunks else config.vtt.text
    wait: bool = args.wait if args.wait is not None else config.vtt.wait

    status_code: int
    payload: dict[str, Any]
    status_code, payload = supadata_provider.request_transcript(
        api_key=api_key,
        url=args.input,
        timeout_seconds=config.network.timeout_seconds,
        mode=mode,
        lang=lang,
        text=text,
        proxy=config.network.proxy or None,
    )

    if status_code == 202 and wait:
        job_id: str = str(payload["jobId"])
        payload = supadata_provider.poll_transcript_job(
            api_key=api_key,
            job_id=job_id,
            timeout_seconds=config.network.timeout_seconds,
            poll_interval_seconds=config.vtt.poll_interval_seconds,
            wait_timeout_seconds=config.vtt.wait_timeout_seconds,
            proxy=config.network.proxy or None,
        )

    return CommandResult(
        ok=True,
        command="vtt",
        provider="supadata",
        data=payload,
        error=None,
        meta={
            **_meta("vtt", config, config_path, verbose),
            "input": args.input,
            "mode": mode,
            "lang": lang,
            "text": text,
            "wait": wait,
            "initial_status_code": status_code,
        },
    )


def run_video(
    args: argparse.Namespace, config: AppConfig, verbose: bool, config_path: Path | None
) -> CommandResult:
    source: str = str(args.input)
    ensure_path_exists(source)
    output_dir: str = (
        str(args.output_dir) if args.output_dir is not None else config.video.output_dir
    )
    max_frames: int = args.max_frames if args.max_frames is not None else config.video.max_frames
    seconds_per_frame: float = (
        args.seconds_per_frame
        if args.seconds_per_frame is not None
        else config.video.seconds_per_frame
    )
    frame_format: str = args.frame_format or config.video.frame_format
    requested_timestamps: list[float] | None = (
        list(args.at_seconds) if args.at_seconds is not None else None
    )
    align_to_segments: bool = (
        args.align_to_segments
        if args.align_to_segments is not None
        else config.video.align_to_segments
    )
    transcribe: bool = args.transcribe if args.transcribe is not None else config.video.transcribe
    timestamps: str = args.timestamps if args.timestamps is not None else config.video.timestamps
    model: str = args.model or config.stt.model
    language: str = args.language if args.language is not None else config.stt.language
    prompt: str = args.prompt if args.prompt is not None else config.stt.prompt

    payload: dict[str, Any] = video_provider.analyze_local_video(
        source=source,
        output_dir=output_dir,
        max_frames=max_frames,
        seconds_per_frame=seconds_per_frame,
        frame_format=frame_format,
        align_to_segments=align_to_segments,
        requested_timestamps=requested_timestamps,
        transcribe=transcribe,
        timeout_seconds=config.network.timeout_seconds,
        model=model,
        language=language,
        timestamps=timestamps,
        temperature=config.stt.temperature,
        prompt=prompt,
        proxy=config.network.proxy or None,
        api_key=config.stt.api_key or "",
        url=config.stt.url or None,
    )
    return CommandResult(
        ok=True,
        command="video",
        provider="local+groq",
        data=payload,
        error=None,
        meta={
            **_meta("video", config, config_path, verbose),
            "input": source,
            "max_frames": max_frames,
            "seconds_per_frame": seconds_per_frame,
            "frame_format": frame_format,
            "requested_timestamps": requested_timestamps or [],
            "align_to_segments": align_to_segments,
            "transcribe": transcribe,
            "timestamps": timestamps,
        },
    )


def run_tts(
    args: argparse.Namespace, config: AppConfig, verbose: bool, config_path: Path | None
) -> CommandResult:
    backend: str = args.backend or config.tts.backend
    model: str = args.model or config.tts.model
    voice: str = args.voice or config.tts.voice
    language: str = args.language if args.language is not None else config.tts.language
    speed: float = args.speed if args.speed is not None else config.tts.speed
    clean_text: bool = args.clean_text if args.clean_text is not None else config.tts.clean_text
    play: bool = args.play if args.play is not None else config.tts.autoplay
    volume: int = args.volume if args.volume is not None else config.tts.volume
    output: str | None = str(args.output) if args.output is not None else None
    save: bool = bool(args.save or output is not None)

    payload: dict[str, Any] = tts_provider.synthesize(
        text=str(args.text),
        model=model,
        voice=voice,
        speed=speed,
        clean_text=clean_text,
        output=output,
        output_dir=config.tts.output_dir,
        save=save,
        play=play,
        volume=volume,
        backend=backend,
        language=language,
    )
    return CommandResult(
        ok=True,
        command="tts",
        provider=str(payload.get("backend") or backend),
        data=payload,
        error=None,
        meta={
            **_meta("tts", config, config_path, verbose),
            "text_chars": len(str(args.text)),
            "backend": str(payload.get("backend") or backend),
            "save": save,
            "play": play,
        },
    )


def run_tg_speak(
    args: argparse.Namespace,
    config: AppConfig,
    tg_config: Any,
    verbose: bool,
    config_path: Path | None,
) -> CommandResult:
    backend: str = args.backend or config.tts.backend
    model: str = args.model or config.tts.model
    voice: str = args.voice or config.tts.voice
    language: str = args.language if args.language is not None else config.tts.language
    speed: float = args.speed if args.speed is not None else config.tts.speed
    clean_text: bool = args.clean_text if args.clean_text is not None else config.tts.clean_text

    payload: dict[str, Any] = tts_provider.synthesize(
        text=str(args.text),
        model=model,
        voice=voice,
        speed=speed,
        clean_text=clean_text,
        output=None,
        output_dir=config.tts.output_dir,
        save=True,
        play=False,
        volume=config.tts.volume,
        backend=backend,
        language=language,
    )
    generated_path: str | None = payload.get("path")
    if not generated_path:
        raise AssistantToolsError(
            "TTS did not return an output file path",
            error_type="tts_write_error",
            exit_code=5,
        )

    try:
        voice_result = _send_voice_via_daemon(
            tg_config,
            args.peer,
            generated_path,
            args.caption,
            args.reply_to,
            args.full,
        )
    finally:
        if payload.get("saved"):
            Path(generated_path).unlink(missing_ok=True)

    data: dict[str, Any] = dict(voice_result.data or {})
    data["tts"] = {
        "backend": payload.get("backend") or backend,
        "voice": voice,
        "model": model,
        "language": payload.get("language") or language,
        "speed": speed,
        "clean_text": clean_text,
        "text_chars": len(str(args.text)),
    }
    meta: dict[str, Any] = dict(voice_result.meta)
    meta.update(
        {
            **_meta("tg.speak", config, config_path, verbose),
            "text_chars": len(str(args.text)),
            "backend": payload.get("backend") or backend,
            "voice": voice,
            "model": model,
            "language": payload.get("language") or language,
            "speed": speed,
            "clean_text": clean_text,
        }
    )
    return CommandResult(
        ok=voice_result.ok,
        command="tg.speak",
        provider=f"{payload.get('backend') or backend}+telethon",
        data=data,
        error=voice_result.error,
        meta=meta,
    )


def _toml_value(val: str) -> str:
    """Format a string value for TOML config."""
    if val.lower() in ("true", "false"):
        return val.lower()
    try:
        int(val)
        return val
    except ValueError:
        pass
    try:
        float(val)
        return val
    except ValueError:
        pass
    return f"'{val}'"


_VIDEO_EXTENSIONS: set[str] = {".mp4", ".avi", ".mkv", ".mov", ".webm", ".flv", ".wmv", ".m4v"}


def _validate_video_if_needed(path: str) -> None:
    """Run ffprobe on video files to catch corrupted files before sending."""
    import subprocess as _subprocess

    p = Path(path)
    if p.suffix.lower() not in _VIDEO_EXTENSIONS:
        return
    if not p.exists():
        raise AssistantToolsError(f"File not found: {path}", error_type="file_not_found")
    result = _subprocess.run(
        ["ffprobe", "-v", "error", "-i", path, "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssistantToolsError(
            f"Video file appears corrupted or invalid: {path}\n{result.stderr.strip()}",
            error_type="invalid_video",
        )


def _run_tg_stt(args: Any, config: AppConfig, tg_config: Any) -> CommandResult:
    """Download voice/audio message and transcribe."""
    import shutil

    download_result: CommandResult = tg_commands.run(
        tg_commands.media_download(tg_config, args.peer, args.message_id, None, False)
    )
    if not download_result.ok:
        return download_result
    dl_data: dict[str, Any] = download_result.data or {}
    dl_path: str = dl_data.get("path", "")
    if not dl_path:
        return CommandResult(
            ok=False,
            command="tg.stt",
            provider="groq",
            data=None,
            error={"type": "no_media", "message": "Message has no downloadable media"},
            meta={},
        )
    # Rename to .ogg for whisper compatibility
    ogg_path: str = dl_path if dl_path.endswith(".ogg") else f"{dl_path.rsplit('.', 1)[0]}.ogg"
    if ogg_path != dl_path:
        shutil.copy2(dl_path, ogg_path)
    api_key: str = config.stt.api_key or require_env("GROQ_API_KEY")
    result_data: dict[str, Any] = groq_provider.transcribe(
        source=ogg_path,
        api_key=api_key,
        model=config.stt.model,
        language=args.language or "",
        url=config.stt.url,
        timeout_seconds=60,
        timestamps="none",
        temperature=0.0,
        prompt="",
        proxy=None,
    )
    text: str = result_data.get("text", "")
    return CommandResult(
        ok=True,
        command="tg.stt",
        provider="groq",
        data={"text": text, "source_path": dl_path, "message_id": args.message_id},
        error=None,
        meta={"peer": args.peer, "model": config.stt.model},
    )


def _ensure_daemon(tg_config: Any) -> None:
    """Start the daemon if not already running."""
    import subprocess as _sp
    import time as _t
    from assistant_tools.tg.daemon import SOCKET_PATH as _SOCK

    if _SOCK.exists():
        return

    log_path = tg_config.session_file.parent / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("ab")
    try:
        _sp.Popen(
            ["kit", "tg", "--profile", tg_config.profile, "_daemon"],
            start_new_session=True,
            stdout=log_file,
            stderr=log_file,
        )
    finally:
        log_file.close()

    deadline: float = _t.time() + 5.0
    while not _SOCK.exists() and _t.time() < deadline:
        _t.sleep(0.1)


async def _daemon_request(request: dict[str, Any]) -> dict[str, Any]:
    """Send a request to the running daemon and return the response."""
    import asyncio as _aio
    from assistant_tools.tg.daemon import IPC_STREAM_LIMIT as _IPC_STREAM_LIMIT
    from assistant_tools.tg.daemon import SOCKET_PATH as _SOCK

    if not _SOCK.exists():
        return {"ok": False, "error": "daemon not running (no socket)"}
    try:
        reader, writer = await _aio.open_unix_connection(str(_SOCK), limit=_IPC_STREAM_LIMIT)
        writer.write(json.dumps(request, ensure_ascii=False).encode() + b"\n")
        await writer.drain()
        line: bytes = await reader.readline()
        writer.close()
        await writer.wait_closed()
        return json.loads(line.decode())
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _daemon_response_is_locked(response: dict[str, Any]) -> bool:
    error: str = str(response.get("error", "")).lower()
    return DAEMON_LOCKED_ERROR in error


def _request_daemon_with_recovery(request: dict[str, Any], tg_config: Any) -> dict[str, Any]:
    import time as _t
    from assistant_tools.tg.daemon import SOCKET_PATH as _SOCK

    resp = _asyncio.run(_daemon_request(request))
    error = str(resp.get("error", ""))
    if not resp.get("ok") and (
        resp.get("error") == "daemon not running (no socket)" or "Connection refused" in error
    ):
        _SOCK.unlink(missing_ok=True)
        _ensure_daemon(tg_config)
        if not _SOCK.exists():
            return resp
        resp = _asyncio.run(_daemon_request(request))

    if not _daemon_response_is_locked(resp):
        return resp

    for delay in (0.2, 0.5, 1.0):
        _t.sleep(delay)
        resp = _asyncio.run(_daemon_request(request))
        if not _daemon_response_is_locked(resp):
            return resp

    _asyncio.run(_daemon_request({"cmd": "shutdown"}))
    deadline = _t.time() + 3.0
    while _SOCK.exists() and _t.time() < deadline:
        _t.sleep(0.1)
    if _SOCK.exists():
        return resp

    _ensure_daemon(tg_config)
    if not _SOCK.exists():
        return resp
    return _asyncio.run(_daemon_request(request))


def _send_voice_via_daemon(
    tg_config: Any,
    peer: str,
    path: str,
    caption: str | None,
    reply_to: int | None,
    full: bool,
) -> CommandResult:
    _ensure_daemon(tg_config)
    request = {
        "cmd": "send_voice",
        "peer": peer,
        "path": path,
        "caption": caption,
        "reply_to": reply_to,
        "full": full,
    }
    resp = _request_daemon_with_recovery(request, tg_config)
    return CommandResult(
        ok=resp.get("ok", False),
        command="tg.send-voice",
        provider="telethon+daemon",
        data=resp.get("data"),
        error=resp.get("error"),
        meta={"daemon": True, "profile": tg_config.profile},
    )


def _daemon_middleware(args: Any, tg_config: Any) -> CommandResult | None:
    """Try to proxy command through daemon. Returns None to fall through to direct."""
    from assistant_tools.tg.daemon import SOCKET_PATH as _SOCK

    # Auto-start daemon on first use
    if not _SOCK.exists():
        _ensure_daemon(tg_config)

    if not _SOCK.exists():
        return None

    cmd: str = args.tg_command
    request: dict[str, Any] | None = None

    if cmd == "send":
        request = {
            "cmd": "send",
            "peer": args.peer,
            "text": args.text,
            "reply_to": args.reply_to,
            "full": args.full,
            "parse_mode": getattr(args, "parse_mode", None),
        }
    elif cmd in ("send-media", "send-photo"):
        _paths: list[str] = [str(p) for p in args.path]  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportUnknownVariableType]
        if len(_paths) == 1:
            request = {
                "cmd": "send_media",
                "peer": args.peer,
                "path": _paths[0],
                "caption": args.caption,
                "reply_to": args.reply_to,
                "full": args.full,
                "force_video": not getattr(args, "as_gif", False),
            }
        else:
            return None  # Albums fall through to direct
    elif cmd == "send-file":
        request = {
            "cmd": "send_file",
            "peer": args.peer,
            "path": str(args.path),
            "caption": args.caption,
            "reply_to": args.reply_to,
            "full": args.full,
        }
    elif cmd == "history":
        request = {
            "cmd": "history",
            "peer": args.peer,
            "limit": args.limit,
            "offset_id": args.offset_id,
            "full": args.full,
        }
    elif cmd == "get":
        request = {
            "cmd": "get",
            "peer": args.peer,
            "message_ids": list(args.message_ids),
            "full": args.full,
        }
    elif cmd == "resolve":
        request = {"cmd": "resolve", "peer": args.peer}
    elif cmd == "find-dialog":
        request = {"cmd": "find_dialog", "query": args.query, "limit": args.limit}
    elif cmd == "forward":
        request = {
            "cmd": "forward",
            "from_peer": args.from_peer,
            "to_peer": args.to_peer,
            "message_ids": list(args.message_ids),
        }
    elif cmd == "edit":
        request = {
            "cmd": "edit",
            "peer": args.peer,
            "message_id": args.message_id,
            "text": args.text,
            "parse_mode": getattr(args, "parse_mode", None),
        }
    elif cmd == "delete":
        request = {"cmd": "delete", "peer": args.peer, "message_ids": list(args.message_ids)}
    elif cmd == "react":
        request = {
            "cmd": "react",
            "peer": args.peer,
            "message_id": args.message_id,
            "emoji": args.emoji,
        }
    elif cmd == "send-voice":
        request = {
            "cmd": "send_voice",
            "peer": args.peer,
            "path": str(args.path),
            "caption": args.caption,
            "reply_to": args.reply_to,
            "full": args.full,
        }
    elif cmd == "search":
        request = {
            "cmd": "search",
            "peer": args.peer,
            "query": args.query,
            "limit": args.limit,
            "full": args.full,
        }
    elif cmd == "media-download":
        request = {
            "cmd": "media_download",
            "peer": args.peer,
            "message_ids": list(args.message_ids),
            "output_dir": args.output_dir,
            "full": args.full,
        }
    elif cmd == "media-info":
        request = {
            "cmd": "media_info",
            "peer": args.peer,
            "message_id": args.message_id,
            "full": args.full,
        }
    elif cmd == "wait-next":
        request = {"cmd": "wait_next", "peers": list(args.peer), "timeout": args.timeout}
    elif cmd == "dialogs":
        request = {"cmd": "dialogs", "limit": args.limit, "full": args.full}

    if request is None:
        return None

    resp = _request_daemon_with_recovery(request, tg_config)

    return CommandResult(
        ok=resp.get("ok", False),
        command=f"tg.{cmd}",
        provider="telethon+daemon",
        data=resp.get("data"),
        error=resp.get("error"),
        meta={"daemon": True, "profile": tg_config.profile},
    )


def dispatch(
    args: argparse.Namespace, config: AppConfig, config_path: Path | None
) -> CommandResult:
    verbose: bool = bool(args.verbose)
    if args.command == "stt":
        return run_stt(args, config, verbose, config_path)
    if args.command == "search":
        return run_search(args, config, verbose, config_path)
    if args.command == "extract":
        return run_extract(args, config, verbose, config_path)
    if args.command == "vtt":
        return run_vtt(args, config, verbose, config_path)
    if args.command == "video":
        return run_video(args, config, verbose, config_path)
    if args.command == "tts":
        return run_tts(args, config, verbose, config_path)
    if args.command == "shardx":
        return shardx_provider.run(args)
    if args.command == "config":
        from assistant_tools.config import DEFAULT_CONFIG_PATH

        config_path_resolved: Path = (config_path or DEFAULT_CONFIG_PATH).expanduser()
        cmd: str = getattr(args, "config_command", "show") or "show"
        if cmd == "show":
            if config_path_resolved.exists():
                print(config_path_resolved.read_text())
            else:
                print(f"No config file at {config_path_resolved}")
            return CommandResult(
                ok=True,
                command="config.show",
                provider="local",
                data={},
                error=None,
                meta={"path": str(config_path_resolved)},
            )
        if cmd == "path":
            print(str(config_path_resolved))
            return CommandResult(
                ok=True,
                command="config.path",
                provider="local",
                data={"path": str(config_path_resolved)},
                error=None,
                meta={},
            )
        if cmd == "set":
            import re as _re

            content: str = config_path_resolved.read_text() if config_path_resolved.exists() else ""
            section: str
            sep: str
            key: str
            section, sep, key = args.key.rpartition(".")
            if not sep:
                section, key = "default", args.key
            lines: list[str] = content.splitlines(keepends=True)
            in_section: bool = False
            replaced: bool = False
            section_found: bool = False
            for i, line in enumerate(lines):
                stripped: str = line.strip()
                if stripped == f"[{section}]":
                    in_section = True
                    section_found = True
                    continue
                if stripped.startswith("[") and stripped.endswith("]"):
                    if in_section and not replaced:
                        lines.insert(i, f"{key} = {_toml_value(args.value)}\n")
                        replaced = True
                    in_section = False
                    continue
                if in_section and _re.match(rf"^{_re.escape(key)}\s*=", stripped):
                    lines[i] = f"{key} = {_toml_value(args.value)}\n"
                    replaced = True
            if not replaced:
                if not section_found:
                    lines.append(f"\n[{section}]\n")
                lines.append(f"{key} = {_toml_value(args.value)}\n")
            content = "".join(lines)
            config_path_resolved.parent.mkdir(parents=True, exist_ok=True)
            config_path_resolved.write_text(content)
            print(f"Set {args.key} = {args.value}")
            return CommandResult(
                ok=True,
                command="config.set",
                provider="local",
                data={"key": args.key, "value": args.value},
                error=None,
                meta={},
            )
    if args.command == "tg":
        tg_config = resolve_tg_config(config, args.profile)

        # Internal daemon process
        if args.tg_command == "_daemon":
            from assistant_tools.tg.daemon import run_daemon

            _asyncio.run(run_daemon(tg_config))
            return CommandResult(
                ok=True, command="tg._daemon", provider="telethon", data={}, error=None, meta={}
            )

        # Daemon middleware: transparently proxies supported commands
        if args.tg_command not in ("auth", "speak", "copy", "stt", "participants"):
            result: CommandResult | None = _daemon_middleware(args, tg_config)
            if result is not None:
                return result

        # Validate video files before send-media (catches corrupted files early)
        if args.tg_command in ("send-media", "send-photo", "send-file"):
            _vpaths: list[str] = (
                [str(p) for p in args.path] if args.tg_command != "send-file" else [str(args.path)]
            )  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportUnknownVariableType]
            for p in _vpaths:
                _validate_video_if_needed(p)
        if args.tg_command == "auth":
            if args.tg_auth_command == "login":
                return tg_commands.run(tg_commands.auth_login(tg_config, args.phone))
            if args.tg_auth_command == "status":
                return tg_commands.run(tg_commands.auth_status(tg_config))
            if args.tg_auth_command == "logout":
                return tg_commands.run(tg_commands.auth_logout(tg_config))
            if args.tg_auth_command == "export-session":
                return tg_commands.run(tg_commands.auth_export_session(tg_config))
            if args.tg_auth_command == "import-session":
                return tg_commands.run(
                    tg_commands.auth_import_session(tg_config, args.session_string)
                )
        if args.tg_command == "resolve":
            return tg_commands.run(tg_commands.resolve_peer(tg_config, args.peer))
        if args.tg_command == "miniapp":
            if args.miniapp_command == "main":
                return tg_commands.run(
                    tg_commands.miniapp_main_url(
                        tg_config,
                        args.bot,
                        args.start_param,
                        args.platform,
                        args.compact,
                        args.fullscreen,
                    )
                )
            if args.miniapp_command == "menu":
                return tg_commands.run(
                    tg_commands.miniapp_menu_url(
                        tg_config,
                        args.bot,
                        args.start_param,
                        args.platform,
                        args.compact,
                        args.fullscreen,
                    )
                )
            if args.miniapp_command == "open":
                return tg_commands.run(
                    tg_commands.miniapp_open(
                        tg_config,
                        args.bot,
                        args.identity,
                        args.source,
                        args.start_param,
                        args.platform,
                        args.compact,
                        args.fullscreen,
                        args.wait_ms,
                        args.live,
                    )
                )
        if args.tg_command == "find-dialog":
            return tg_commands.run(
                tg_commands.find_dialog(
                    tg_config,
                    query=str(args.query),
                    limit=args.limit,
                )
            )
        if args.tg_command == "dialogs":
            return tg_commands.run(tg_commands.dialogs(tg_config, args.limit, args.full))
        if args.tg_command == "participants":
            return tg_commands.run(tg_commands.participants(tg_config, args.peer, args.limit))
        if args.tg_command == "history":
            return tg_commands.run(
                tg_commands.history(tg_config, args.peer, args.limit, args.offset_id, args.full)
            )
        if args.tg_command == "get":
            return tg_commands.run(
                tg_commands.get_messages(tg_config, args.peer, list(args.message_ids), args.full)
            )
        if args.tg_command == "send":
            return tg_commands.run(
                tg_commands.send_message(
                    tg_config, args.peer, args.text, args.reply_to, args.full, args.parse_mode
                )
            )
        if args.tg_command == "send-file":
            return tg_commands.run(
                tg_commands.send_file(
                    tg_config, args.peer, str(args.path), args.caption, args.reply_to, args.full
                )
            )
        if args.tg_command in ("send-media", "send-photo"):
            _paths: list[str] = [str(p) for p in args.path]  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType, reportUnknownVariableType]
            if len(_paths) == 1:
                as_gif: bool = getattr(args, "as_gif", False)
                return tg_commands.run(
                    tg_commands.send_media(
                        tg_config,
                        args.peer,
                        _paths[0],
                        args.caption,
                        args.reply_to,
                        args.full,
                        force_video=not as_gif,
                    )
                )
            else:
                as_gif = getattr(args, "as_gif", False)
                return tg_commands.run(
                    tg_commands.send_album(
                        tg_config,
                        args.peer,
                        _paths,
                        args.caption,
                        args.reply_to,
                        args.full,
                        force_video=not as_gif,
                    )
                )
        if args.tg_command == "send-voice":
            return tg_commands.run(
                tg_commands.send_voice(
                    tg_config, args.peer, str(args.path), args.caption, args.reply_to, args.full
                )
            )
        if args.tg_command == "speak":
            return run_tg_speak(args, config, tg_config, verbose, config_path)
        if args.tg_command == "react":
            return tg_commands.run(
                tg_commands.react(tg_config, args.peer, args.message_id, args.emoji)
            )
        if args.tg_command == "search":
            return tg_commands.run(
                tg_commands.search_messages(tg_config, args.peer, args.query, args.limit, args.full)
            )
        if args.tg_command == "wait-next":
            return tg_commands.run(
                tg_commands.wait_next_message(
                    tg_config,
                    list(args.peer),
                    args.timeout,
                    args.full,
                )
            )
        if args.tg_command == "media-info":
            return tg_commands.run(
                tg_commands.media_info(tg_config, args.peer, args.message_id, args.full)
            )
        if args.tg_command == "media-download":
            return tg_commands.run(
                tg_commands.media_download(
                    tg_config, args.peer, list(args.message_ids), args.output_dir, args.full
                )
            )
        if args.tg_command == "copy":
            return tg_commands.run(
                tg_commands.copy_message(
                    tg_config, args.source_peer, args.message_id, args.target_peer, args.full
                )
            )
        if args.tg_command == "forward":
            return tg_commands.run(
                tg_commands.forward_message(
                    tg_config, args.from_peer, args.to_peer, list(args.message_ids)
                )
            )
        if args.tg_command == "edit":
            return tg_commands.run(
                tg_commands.edit_message(
                    tg_config, args.peer, args.message_id, args.text, args.parse_mode
                )
            )
        if args.tg_command == "delete":
            return tg_commands.run(
                tg_commands.delete_message(tg_config, args.peer, list(args.message_ids))
            )
        if args.tg_command == "stt":
            return _run_tg_stt(args, config, tg_config)
    raise AssistantToolsError(
        f"Unknown command: {args.command}",
        error_type="unknown_command",
        exit_code=2,
    )


def main() -> None:
    parser: argparse.ArgumentParser = build_parser()
    args: argparse.Namespace = parser.parse_args()
    config_path: Path | None = args.config
    try:
        config: AppConfig = load_config(config_path)
        result: CommandResult = dispatch(args, config, config_path)
        emit_result(result)
    except AssistantToolsError as err:
        fallback_command: str = getattr(args, "command", "unknown")
        result = error_result(
            command=fallback_command,
            provider="unknown",
            error_type=err.error_type,
            message=str(err),
            meta={"command": fallback_command},
        )
        emit_result(result)
        raise SystemExit(err.exit_code) from err
