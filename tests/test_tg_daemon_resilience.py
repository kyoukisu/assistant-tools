from __future__ import annotations

import asyncio
import fcntl
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from assistant_tools import cli
from assistant_tools.tg import daemon


def test_daemon_lock_allows_only_one_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "daemon.lock"
    first = daemon._acquire_daemon_lock(lock_path)  # pyright: ignore[reportPrivateUsage]
    assert first is not None
    assert lock_path.read_text() == str(os.getpid())

    second = daemon._acquire_daemon_lock(lock_path)  # pyright: ignore[reportPrivateUsage]
    assert second is None

    fcntl.flock(first.fileno(), fcntl.LOCK_UN)
    first.close()
    third = daemon._acquire_daemon_lock(lock_path)  # pyright: ignore[reportPrivateUsage]
    assert third is not None
    fcntl.flock(third.fileno(), fcntl.LOCK_UN)
    third.close()


def test_daemon_request_retries_sqlite_lock(monkeypatch: Any) -> None:
    responses = [
        {"ok": False, "error": "database is locked"},
        {"ok": True, "data": {"message": "sent"}},
    ]
    sleeps: list[float] = []

    async def fake_request(request: dict[str, Any]) -> dict[str, Any]:
        return responses.pop(0)

    monkeypatch.setattr(cli, "_daemon_request", fake_request)
    monkeypatch.setattr("time.sleep", sleeps.append)

    result = cli._request_daemon_with_recovery(  # pyright: ignore[reportPrivateUsage]
        {"cmd": "send", "peer": "me", "text": "ok"},
        SimpleNamespace(profile="main"),
    )

    assert result["ok"]
    assert sleeps == [0.2]


def test_send_voice_uses_daemon_transport(monkeypatch: Any, tmp_path: Path) -> None:
    voice_path = tmp_path / "voice.ogg"
    voice_path.write_bytes(b"voice")
    requests: list[dict[str, Any]] = []

    def fake_ensure_daemon(config: Any) -> None:
        return None

    monkeypatch.setattr(cli, "_ensure_daemon", fake_ensure_daemon)

    def fake_request(request: dict[str, Any], config: Any) -> dict[str, Any]:
        requests.append(request)
        return {"ok": True, "data": {"message": {"message_id": 42}}}

    monkeypatch.setattr(cli, "_request_daemon_with_recovery", fake_request)
    result = cli._send_voice_via_daemon(  # pyright: ignore[reportPrivateUsage]
        SimpleNamespace(profile="main"),
        "971271110",
        str(voice_path),
        None,
        10,
        False,
    )

    assert result.ok
    assert result.provider == "telethon+daemon"
    assert requests == [
        {
            "cmd": "send_voice",
            "peer": "971271110",
            "path": str(voice_path),
            "caption": None,
            "reply_to": 10,
            "full": False,
        }
    ]


def test_daemon_request_accepts_large_response(
    monkeypatch: Any, tmp_path: Path
) -> None:
    async def run() -> None:
        socket_path = tmp_path / "large-response.sock"
        monkeypatch.setattr(daemon, "SOCKET_PATH", socket_path)

        async def respond(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            await reader.readline()
            payload = '{"ok":true,"data":{"value":"' + ("x" * 100_000) + '"}}\n'
            writer.write(payload.encode())
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_unix_server(respond, path=str(socket_path))
        async with server:
            result = await cli._daemon_request({"cmd": "ping"})  # pyright: ignore[reportPrivateUsage]
        assert result["ok"]
        assert len(result["data"]["value"]) == 100_000

    asyncio.run(run())


def test_shutdown_request_sets_event() -> None:
    async def run() -> None:
        shutdown_event = asyncio.Event()
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"cmd":"shutdown"}\n')
        reader.feed_eof()

        class Writer:
            def __init__(self) -> None:
                self.data = b""

            def write(self, data: bytes) -> None:
                self.data += data

            async def drain(self) -> None:
                return None

            def close(self) -> None:
                return None

            async def wait_closed(self) -> None:
                return None

        writer = Writer()
        await daemon.handle_client(
            reader,
            writer,  # type: ignore[arg-type]
            SimpleNamespace(),  # type: ignore[arg-type]
            shutdown_event,
        )
        assert shutdown_event.is_set()
        assert b"shutting down" in writer.data

    asyncio.run(run())
