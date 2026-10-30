# SPDX-License-Identifier: MIT
"""Move an installed plugin directory out of the plugin roots.

The package format uses the platform trash as the recovery window for an
update or a removal. This module implements the freedesktop.org trash layout on
Linux, the user Trash directory on macOS, and a temporary directory fallback
elsewhere. A directory that moves to the fallback does not survive a reboot.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime


def move_to_trash(path: str) -> str:
    """Move one path to the best available trash location.

    :param path: Absolute path to move.
    :returns: Destination path.
    :rtype: str
    :raises OSError: If no trash location accepts the path.
    """
    path = os.path.abspath(path)
    errors: list[str] = []

    for mover in (_xdg_trash, _macos_trash, _temporary_trash):
        try:
            destination = mover(path)
        except OSError as exc:
            errors.append(str(exc))
            continue
        if destination is not None:
            return destination

    detail = "; ".join(errors) if errors else "no writable trash directory"
    raise OSError(f"cannot move {path} to the trash: {detail}")


def _xdg_trash(path: str) -> str | None:
    if os.name != "posix" or os.uname().sysname != "Linux":
        return None
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    root = os.path.join(data_home, "Trash")
    files = os.path.join(root, "files")
    info = os.path.join(root, "info")
    os.makedirs(files, exist_ok=True)
    os.makedirs(info, exist_ok=True)
    stamp = _stamp()
    name = f"{os.path.basename(path)}.{stamp}"
    destination = os.path.join(files, name)
    shutil.move(path, destination)
    record = os.path.join(info, name + ".trashinfo")
    with open(record, "w", encoding="utf-8") as handle:
        handle.write("[Trash Info]\n")
        handle.write(f"Path={path}\n")
        handle.write(f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n")
    return destination


def _macos_trash(path: str) -> str | None:
    if os.name != "posix" or os.uname().sysname != "Darwin":
        return None
    root = os.path.expanduser("~/.Trash")
    os.makedirs(root, exist_ok=True)
    destination = os.path.join(root, f"{os.path.basename(path)}.{_stamp()}")
    shutil.move(path, destination)
    return destination


def _temporary_trash(path: str) -> str | None:
    root = os.path.join(tempfile.gettempdir(), "mloader-trash")
    os.makedirs(root, exist_ok=True)
    destination = os.path.join(root, f"{os.path.basename(path)}.{_stamp()}")
    shutil.move(path, destination)
    return destination


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S") + f"-{os.getpid()}"
