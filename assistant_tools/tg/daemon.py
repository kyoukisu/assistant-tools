"""Minimal Telegram daemon — single persistent connection, push-based wait-next."""
# pyright: reportMissingTypeStubs=false, reportPrivateUsage=false, reportUnusedFunction=false

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import shutil
import tempfile
import time as _time
import traceback
from pathlib import Path
from typing import Any
from typing import TextIO

from telethon import TelegramClient

from assistant_tools.tg import commands as _cmds
from assistant_tools.tg.client import _get_daemon_client
from assistant_tools.tg.client import _set_daemon_client
from assistant_tools.tg.client import make_client
from assistant_tools.tg.config import ResolvedTgConfig

SOCKET_PATH: Path = Path(tempfile.gettempdir()) / "kit-tg-daemon.sock"
LOCK_PATH: Path = Path(tempfile.gettempdir()) / "kit-tg-daemon.lock"
IDLE_TIMEOUT: float = 600.0  # 10 minutes

_last_activity: float = 0.0
_pending_messages: dict[int, list[dict[str, Any]]] = {}
_waiters: list[tuple[asyncio.Event, list[int], list[dict[str, Any] | None]]] = []


def _touch() -> None:
    global _last_activity
    _last_activity = _time.time()


def _acquire_daemon_lock(path: Path = LOCK_PATH) -> TextIO | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    return lock_file


def _peer_id_from_event(event: Any) -> int:
    chat: Any = getattr(event, "chat_id", None)
    if chat is not None:
        return int(chat)
    peer: Any = getattr(event, "peer_id", None)
    if peer is not None:
        return int(peer)
    msg: Any = getattr(event, "message", None)
    if msg is not None:
        chat_id: Any = getattr(msg, "chat_id", None) or getattr(msg, "peer_id", None)
        if chat_id is not None:
            return int(chat_id)
    return 0


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    tg_config: ResolvedTgConfig,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    _touch()
    try:
        data: bytes = await reader.readline()
        request: dict[str, Any] = json.loads(data.decode())
        cmd: str = request.get("cmd", "")
        result: dict[str, Any] = {"ok": False, "error": f"unknown command: {cmd}"}

        if cmd == "ping":
            result = {"ok": True, "data": "pong"}

        elif cmd == "shutdown":
            result = {"ok": True, "data": "shutting down"}
            if shutdown_event is not None:
                shutdown_event.set()

        elif cmd == "send":
            r = await _cmds.send_message(
                tg_config, request["peer"], request["text"],
                request.get("reply_to"), request.get("full", False),
                request.get("parse_mode"),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "send_media":
            r = await _cmds.send_media(
                tg_config, request["peer"], request["path"],
                request.get("caption"), request.get("reply_to"),
                request.get("full", False),
                request.get("force_video", False),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "send_file":
            r = await _cmds.send_file(
                tg_config, request["peer"], request["path"],
                request.get("caption"), request.get("reply_to"),
                request.get("full", False),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "history":
            r = await _cmds.history(
                tg_config, request["peer"],
                request.get("limit", 20), request.get("offset_id", 0),
                request.get("full", False),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "get":
            r = await _cmds.get_messages(
                tg_config, request["peer"],
                request.get("message_ids", []),
                request.get("full", False),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "resolve":
            r = await _cmds.resolve_peer(tg_config, request["peer"])
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "find_dialog":
            r = await _cmds.find_dialog(
                tg_config, query=request["query"],
                limit=request.get("limit", 20),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "forward":
            r = await _cmds.forward_message(
                tg_config, request["from_peer"], request["to_peer"],
                request.get("message_ids", []),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "edit":
            r = await _cmds.edit_message(
                tg_config, request["peer"], request["message_id"],
                request["text"], request.get("parse_mode"),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "delete":
            r = await _cmds.delete_message(
                tg_config, request["peer"], request.get("message_ids", []),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "send_voice":
            r = await _cmds.send_voice(
                tg_config, request["peer"], request["path"],
                request.get("caption"), request.get("reply_to"),
                request.get("full", False),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "search":
            r = await _cmds.search_messages(
                tg_config, request["peer"], request["query"],
                request.get("limit", 20), request.get("full", False),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "media_download":
            r = await _cmds.media_download(
                tg_config, request["peer"], request.get("message_ids", []),
                request.get("output_dir"), request.get("full", False),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "media_info":
            r = await _cmds.media_info(
                tg_config, request["peer"], request["message_id"],
                request.get("full", False),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "react":
            r = await _cmds.react(
                tg_config, request["peer"],
                request["message_id"], request["emoji"],
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        elif cmd == "wait_next":
            peers: list[str] = request.get("peers", [])
            timeout: float = request.get("timeout", 0)
            r = await _wait_next_push(tg_config, peers, timeout)
            result = {"ok": True, "data": r}

        elif cmd == "dialogs":
            r = await _cmds.dialogs(
                tg_config, request.get("limit", 20), request.get("full", False),
            )
            result = {"ok": r.ok, "data": r.data, "error": r.error}

        writer.write(json.dumps(result, ensure_ascii=False).encode() + b"\n")
        await writer.drain()
    except Exception as exc:
        traceback.print_exc()
        try:
            err_resp: dict[str, Any] = {"ok": False, "error": str(exc)}
            writer.write(json.dumps(err_resp, ensure_ascii=False).encode() + b"\n")
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        await writer.wait_closed()


async def _wait_next_push(
    tg_config: ResolvedTgConfig, peers: list[str], timeout: float,
) -> dict[str, Any]:
    """Wait for new message on any of the given peers using push updates (no polling)."""

    client: TelegramClient | None = _get_daemon_client()
    if client is None:
        # Fall back to polling if daemon client not available
        return {"error": "daemon not ready"}

    # Resolve peer strings to entity IDs
    peer_ids: list[int] = []
    peer_map: dict[int, str] = {}
    for peer_str in peers:
        entity: Any = await _cmds._resolve_peer_entity(client, peer_str)
        eid: int = int(getattr(entity, "id", 0) or 0)
        if isinstance(entity, type):
            from telethon.tl.types import InputPeerSelf
            if isinstance(entity, InputPeerSelf) or not eid:
                me: Any = await client.get_me()
                eid = int(getattr(me, "id", 0) or 0)
        if eid:
            peer_ids.append(eid)
            peer_map[eid] = peer_str

    # Check cache first
    for pid in peer_ids:
        cached: list[dict[str, Any]] = _pending_messages.get(pid, [])
        if cached:
            msg = cached.pop(0)
            return {"message": msg, "peer": peer_map.get(pid, str(pid))}

    # Wait for new message event
    event: asyncio.Event = asyncio.Event()
    result_holder: list[dict[str, Any] | None] = [None]
    _waiters.append((event, peer_ids, result_holder))

    try:
        if timeout > 0:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        else:
            await event.wait()
    except asyncio.TimeoutError:
        return {"error": f"No response within {timeout}s"}
    finally:
        # Clean up waiter
        to_remove: list[int] = []
        for i, (ev, _, _) in enumerate(_waiters):
            if ev is event:
                to_remove.append(i)
        for i in reversed(to_remove):
            _waiters.pop(i)

    if result_holder[0] is not None:
        return {"message": result_holder[0]}

    # Double-check cache after event
    for pid in peer_ids:
        cached = _pending_messages.get(pid, [])
        if cached:
            msg = cached.pop(0)
            return {"message": msg, "peer": peer_map.get(pid, str(pid))}

    return {"error": "no message received"}


async def _on_new_message(event: Any) -> None:
    """Handle telethon NewMessage event — cache message and notify waiters."""
    from assistant_tools.tg.normalize import normalize_message

    _touch()
    msg: Any = getattr(event, "message", None)
    if msg is None:
        return
    pid: int = _peer_id_from_event(event)
    if not pid:
        return
    chat: Any = getattr(event, "_chat", None) or getattr(msg, "chat", None)
    normalized: dict[str, Any] = normalize_message(msg, chat_entity=chat, full=False)
    normalized["peer_id"] = pid

    # Cache for wait_next
    _pending_messages.setdefault(pid, []).append(normalized)
    # Trim cache
    if len(_pending_messages.get(pid, [])) > 100:
        _pending_messages[pid] = _pending_messages[pid][-50:]

    # Notify matching waiters
    done_indices: list[int] = []
    for i, (ev, peer_list, holder) in enumerate(_waiters):
        if pid in peer_list:
            holder[0] = normalized
            ev.set()
            done_indices.append(i)
    for i in reversed(done_indices):
        _waiters.pop(i)


async def run_daemon(tg_config: ResolvedTgConfig) -> None:
    """Start the daemon: connect, register handlers, serve Unix socket."""
    lock_file = _acquire_daemon_lock()
    if lock_file is None:
        print("kit tg daemon already running", flush=True)
        return

    client: TelegramClient | None = None
    server: asyncio.AbstractServer | None = None
    shutdown_event = asyncio.Event()
    try:
        # Only the singleton lock owner may replace the daemon session or socket.
        daemon_session: Path = (
            tg_config.session_file.parent / f"{tg_config.profile}_daemon.session"
        )
        if tg_config.session_file.exists():
            shutil.copy2(str(tg_config.session_file), str(daemon_session))

        from dataclasses import replace
        daemon_config: ResolvedTgConfig = replace(tg_config, session_file=daemon_session)
        SOCKET_PATH.unlink(missing_ok=True)

        client = make_client(daemon_config, receive_updates=True)
        await client.connect()
        _set_daemon_client(client)

        me: Any = await client.get_me()
        print(f"kit tg daemon started (user: {me.first_name}, id: {me.id})", flush=True)

        from telethon import events

        @client.on(events.NewMessage(incoming=True))
        async def _handler(event: Any) -> None:
            await _on_new_message(event)

        server = await asyncio.start_unix_server(
            lambda r, w: handle_client(r, w, tg_config, shutdown_event),
            path=str(SOCKET_PATH),
        )
        os.chmod(str(SOCKET_PATH), 0o600)
        _touch()

        async def _idle_watchdog() -> None:
            while not shutdown_event.is_set():
                await asyncio.sleep(30)
                if _time.time() - _last_activity > IDLE_TIMEOUT:
                    shutdown_event.set()

        print(f"socket: {SOCKET_PATH}", flush=True)
        async with server:
            serve_task = asyncio.create_task(server.serve_forever())
            shutdown_task = asyncio.create_task(shutdown_event.wait())
            watchdog = asyncio.create_task(_idle_watchdog())
            await asyncio.wait(
                (serve_task, shutdown_task), return_when=asyncio.FIRST_COMPLETED
            )
            server.close()
            await server.wait_closed()
            for task in (serve_task, shutdown_task, watchdog):
                task.cancel()
            for task in (serve_task, shutdown_task, watchdog):
                try:
                    await task
                except asyncio.CancelledError:
                    pass
    finally:
        SOCKET_PATH.unlink(missing_ok=True)
        _set_daemon_client(None)
        if client is not None:
            try:
                disconnect_result = client.disconnect()
                if disconnect_result is not None:
                    await disconnect_result
            except Exception:
                traceback.print_exc()
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()
