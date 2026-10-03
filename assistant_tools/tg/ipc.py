"""Daemon IPC constants shared by the CLI client and the daemon.

Kept free of Telethon so client-side commands do not pay its import cost.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

SOCKET_PATH: Path = Path(tempfile.gettempdir()) / "kit-tg-daemon.sock"
LOCK_PATH: Path = Path(tempfile.gettempdir()) / "kit-tg-daemon.lock"
IPC_STREAM_LIMIT: int = 16 * 1024 * 1024
