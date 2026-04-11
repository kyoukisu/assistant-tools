from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from assistant_tools.config import config_snapshot
from assistant_tools.config import load_config
from assistant_tools.models import AppConfig
from assistant_tools.models import CommandResult
from assistant_tools.providers import groq as groq_provider
from assistant_tools.providers import parallel as parallel_provider
from assistant_tools.providers import supadata as supadata_provider
from assistant_tools.tg import commands as tg_commands
from assistant_tools.tg.config import resolve_tg_config
from assistant_tools.utils import AssistantToolsError
from assistant_tools.utils import emit_result
from assistant_tools.utils import ensure_path_exists
from assistant_tools.utils import error_result
from assistant_tools.utils import is_url
from assistant_tools.utils import require_env


def build_parser() -> argparse.ArgumentParser:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(prog="kit")
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

    tg_parser = subparsers.add_parser("tg", help="Telegram CLI via Kurigram")
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

    tg_dialogs = tg_subparsers.add_parser("dialogs", help="List Telegram dialogs")
    tg_dialogs.add_argument("--limit", type=int, default=20)

    tg_history = tg_subparsers.add_parser("history", help="Read chat history")
    tg_history.add_argument("peer", help="Peer id, username, or me")
    tg_history.add_argument("--limit", type=int, default=20)
    tg_history.add_argument("--offset-id", type=int, default=0)

    tg_get = tg_subparsers.add_parser("get", help="Get message(s) from a chat")
    tg_get.add_argument("peer", help="Peer id or username")
    tg_get.add_argument("message_ids", nargs="+", type=int, help="One or more message ids")

    tg_send = tg_subparsers.add_parser("send", help="Send text message")
    tg_send.add_argument("peer", help="Target peer")
    tg_send.add_argument("text", help="Message text")
    tg_send.add_argument("--reply-to", type=int, default=None, help="Reply target message id")

    tg_react = tg_subparsers.add_parser("react", help="React to a message")
    tg_react.add_argument("peer", help="Target peer")
    tg_react.add_argument("message_id", type=int, help="Target message id")
    tg_react.add_argument("emoji", help="Reaction emoji")

    tg_search = tg_subparsers.add_parser("search", help="Search messages in a chat")
    tg_search.add_argument("peer", help="Target peer")
    tg_search.add_argument("query", help="Search query")
    tg_search.add_argument("--limit", type=int, default=20)

    tg_media_info = tg_subparsers.add_parser("media-info", help="Show media metadata")
    tg_media_info.add_argument("peer", help="Target peer")
    tg_media_info.add_argument("message_id", type=int, help="Message id")

    tg_media_download = tg_subparsers.add_parser("media-download", help="Download message media")
    tg_media_download.add_argument("peer", help="Target peer")
    tg_media_download.add_argument("message_id", type=int, help="Message id")
    tg_media_download.add_argument("--output-dir", default=None, help="Output directory override")

    tg_copy = tg_subparsers.add_parser("copy", help="Copy message to another chat")
    tg_copy.add_argument("source_peer", help="Source peer")
    tg_copy.add_argument("message_id", type=int, help="Source message id")
    tg_copy.add_argument("target_peer", help="Target peer")

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
    api_key: str = require_env("GROQ_API_KEY")
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
    )

    if status_code == 202 and wait:
        job_id: str = str(payload["jobId"])
        payload = supadata_provider.poll_transcript_job(
            api_key=api_key,
            job_id=job_id,
            timeout_seconds=config.network.timeout_seconds,
            poll_interval_seconds=config.vtt.poll_interval_seconds,
            wait_timeout_seconds=config.vtt.wait_timeout_seconds,
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
    if args.command == "tg":
        tg_config = resolve_tg_config(config)
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
        if args.tg_command == "dialogs":
            return tg_commands.run(tg_commands.dialogs(tg_config, args.limit))
        if args.tg_command == "history":
            return tg_commands.run(
                tg_commands.history(tg_config, args.peer, args.limit, args.offset_id)
            )
        if args.tg_command == "get":
            return tg_commands.run(
                tg_commands.get_messages(tg_config, args.peer, list(args.message_ids))
            )
        if args.tg_command == "send":
            return tg_commands.run(
                tg_commands.send_message(tg_config, args.peer, args.text, args.reply_to)
            )
        if args.tg_command == "react":
            return tg_commands.run(
                tg_commands.react(tg_config, args.peer, args.message_id, args.emoji)
            )
        if args.tg_command == "search":
            return tg_commands.run(
                tg_commands.search_messages(tg_config, args.peer, args.query, args.limit)
            )
        if args.tg_command == "media-info":
            return tg_commands.run(tg_commands.media_info(tg_config, args.peer, args.message_id))
        if args.tg_command == "media-download":
            return tg_commands.run(
                tg_commands.media_download(tg_config, args.peer, args.message_id, args.output_dir)
            )
        if args.tg_command == "copy":
            return tg_commands.run(
                tg_commands.copy_message(
                    tg_config, args.source_peer, args.message_id, args.target_peer
                )
            )
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
