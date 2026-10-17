"""Daemon IPC paths and constants shared by the CLI client and the daemon.

Kept free of Telethon so client-side commands do not pay its import cost.

Socket and lock are per user and per profile. A single shared path made every
profile talk to whichever account's daemon started first, and a fixed name in
the shared /tmp could be pre-created by another local user.
Reported by neko via Telegram, 2026-10.
"""

from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

IPC_STREAM_LIMIT: int = 16 * 1024 * 1024


def _runtime_dir() -> Path:
    """Private per-user directory: $XDG_RUNTIME_DIR/kit-tg, else $TMPDIR/kit-tg-<uid>."""
    candidates: list[Path] = []
    xdg: str | None = os.environ.get("XDG_RUNTIME_DIR")
    if xdg and os.path.isdir(xdg):
        candidates.append(Path(xdg) / "kit-tg")
    candidates.append(Path(tempfile.gettempdir()) / f"kit-tg-{os.getuid()}")
    base: Path = candidates[-1]
    for candidate in candidates:
        try:
            candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError:
            continue
        base = candidate
        break
    else:
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = os.lstat(base)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise RuntimeError(f"unsafe kit tg runtime dir: {base}")
    if stat.S_IMODE(st.st_mode) != 0o700:
        os.chmod(base, 0o700)
    return base


def _profile_slug(profile: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "_", profile) or "default"


def socket_path(profile: str) -> Path:
    return _runtime_dir() / f"{_profile_slug(profile)}.sock"


def lock_path(profile: str) -> Path:
    return _runtime_dir() / f"{_profile_slug(profile)}.lock"
