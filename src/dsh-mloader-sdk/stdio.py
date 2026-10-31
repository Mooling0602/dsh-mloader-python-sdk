# SPDX-License-Identifier: MIT
"""Private standard streams for a hosted plugin process.

The runtime owns file descriptors 0, 1 and 2. Before plugin code runs,
:func:`detach` duplicates them, points the original descriptors at the
null device and replaces the ``sys`` streams. A plugin therefore writes
to the null device through every ordinary route: ``print``,
``sys.stderr``, the ``logging`` and ``warnings`` modules, a stream
captured at import time, ``os.write`` or a child process that inherits a
standard descriptor.

Only the streams this module returns reach the host. ``stdout`` carries
protocol frames, ``diagnostics`` carries runtime failures, and ``stdin``
carries host requests.
"""

from __future__ import annotations

import os
import sys
from typing import IO


class StandardStreams:
    """Streams owned by the runtime instead of the plugin.

    :param stdin: Text stream carrying host requests. It is a duplicate
        of the original descriptor 0.
    :param stdout: Text stream carrying protocol frames. It is a
        duplicate of the original descriptor 1.
    :param diagnostics: Text stream carrying runtime diagnostics. It is
        a duplicate of the original descriptor 2, which the loader
        forwards to its debug log.
    """

    def __init__(self, stdin: IO[str], stdout: IO[str], diagnostics: IO[str]) -> None:
        self.stdin = stdin
        self.stdout = stdout
        self.diagnostics = diagnostics


def _duplicate(fd: int, mode: str, errors: str, buffering: int = -1) -> IO[str]:
    """Return a UTF-8 text stream over a duplicate of one descriptor.

    :param fd: Descriptor to duplicate.
    :param mode:``r`` for a readable stream, ``w`` for a writable one.
    :param errors: Codec error handler used when a character cannot be
        represented.
    :param buffering: Buffering mode passed to :func:`os.fdopen`.
    :returns: The duplicated text stream.
    :rtype: typing.IO
    :raises OSError: If the descriptor cannot be duplicated.
    """
    return os.fdopen(
        os.dup(fd),
        mode,
        buffering=buffering,
        encoding="utf-8",
        errors=errors,
        newline="\n",
    )


def _flush_standard_streams() -> None:
    """Flush the standard streams before their descriptors are replaced."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        flush = getattr(stream, "flush", None)
        if flush is None:
            continue
        try:
            flush()
        except (OSError, ValueError):
            continue


def detach() -> StandardStreams:
    """Move the process standard streams behind runtime-owned streams.

    The returned streams hold duplicates of descriptors 0, 1 and 2. The
    original descriptors are pointed at the null device, so a later
    write to a standard descriptor from Python, a native extension or a
    child process is discarded. ``sys.stdin``, ``sys.stdout``,
    ``sys.stderr`` and their ``__stdin__``, ``__stdout__`` and
    ``__stderr__`` aliases are replaced by null streams of the matching
    mode.

    The function does not isolate the process from its file system.
    Deliberate code executed in the plugin process can still open files
    or a terminal device and reconnect the standard descriptors. The
    streams are a capability boundary for ordinary output, not a
    security sandbox.

    :returns: The private protocol and diagnostics streams.
    :rtype: StandardStreams
    :raises OSError: If a standard descriptor cannot be duplicated.
    """
    streams = StandardStreams(
        _duplicate(0, "r", "replace"),
        _duplicate(1, "w", "backslashreplace"),
        _duplicate(2, "w", "backslashreplace", buffering=1),
    )
    stdin = os.fdopen(
        os.open(os.devnull, os.O_RDONLY), "r", encoding="utf-8", errors="replace"
    )
    stdout = os.fdopen(
        os.open(os.devnull, os.O_WRONLY),
        "w",
        encoding="utf-8",
        errors="backslashreplace",
        newline="\n",
    )
    stderr = os.fdopen(
        os.open(os.devnull, os.O_WRONLY),
        "w",
        encoding="utf-8",
        errors="backslashreplace",
        newline="\n",
    )
    _flush_standard_streams()
    os.dup2(stdin.fileno(), 0)
    os.dup2(stdout.fileno(), 1)
    os.dup2(stderr.fileno(), 2)
    sys.stdin = stdin
    sys.stdout = stdout
    sys.stderr = stderr
    sys.__stdin__ = stdin
    sys.__stdout__ = stdout
    sys.__stderr__ = stderr
    return streams
