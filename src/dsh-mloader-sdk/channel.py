# SPDX-License-Identifier: MIT
"""Newline delimited JSON channel over the plugin standard streams.

Frames are one JSON object per line, terminated by ``\\n``. Standard output is
the protocol channel: any other text written there, including a stray
``print`` in a plugin, is not a frame. Standard error carries diagnostics.

The channel owns the frame ids. A request reserves an id, writes the frame and
blocks until the host answers with the same id. Host requests that arrive while
the plugin waits are dispatched to the handler registered on
:attr:`Channel.on_host_request`, so a long registration sequence does not stall
host event delivery.
"""

from __future__ import annotations

import json
import sys
from typing import IO, Any, Callable

from .errors import HostUnavailable, PluginCallError
from .version import PROTOCOL_VERSION

_HostRequestHandler = Callable[[dict[str, Any]], None]


class Channel:
    """A bidirectional stream of protocol frames.

    :param stdin: Text stream that carries frames from the host.
    :param stdout: Text stream that carries frames to the host.
    """

    def __init__(self, stdin: IO[str], stdout: IO[str]) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._next_id = 1
        self.closed = False
        self.on_host_request: _HostRequestHandler | None = None

    def send(self, frame: dict[str, Any]) -> None:
        """Write one frame and flush it on the protocol stream.

        :param frame: JSON serialisable protocol frame.
        :raises HostUnavailable: If the channel is closed or the write fails.
        """
        if self.closed:
            raise HostUnavailable("channel is closed")
        line = json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            self._stdout.write(line)
            self._stdout.flush()
        except OSError as exc:
            self.closed = True
            raise HostUnavailable(f"failed to write frame: {exc}") from exc

    def notify(self, op: str, payload: dict[str, Any]) -> None:
        """Send a one way notification and return without waiting for a reply.

        The frame carries ``notification: true``, which tells the host that it
        must not answer. A notification consumes a frame id like any request.

        :param op: Operation name of the notification.
        :param payload: Operation payload.
        :raises HostUnavailable: If the channel is closed.
        """
        self.send(
            {
                "v": PROTOCOL_VERSION,
                "id": self._next_id,
                "op": op,
                "payload": payload,
                "notification": True,
            }
        )
        self._next_id += 1

    def read_frame(self) -> dict[str, Any] | None:
        """Read the next protocol frame.

        Blank lines are skipped. A line that is not valid JSON is reported on
        standard error and skipped, because a stray ``print`` in a plugin must
        not stop the plugin. A line that encodes something other than a JSON
        object is skipped as well.

        :returns: The next frame, or ``None`` once the host closed the channel.
        :rtype: dict or None
        """
        while True:
            line = self._stdin.readline()
            if not line:
                self.closed = True
                return None
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                print(f"[dsh_mloader_sdk] ignoring non-JSON line: {line[:120]}", file=sys.stderr)
                continue
            if isinstance(frame, dict):
                return frame

    def request(self, op: str, payload: dict[str, Any] | None = None) -> Any:
        """Send a request and block until the host answers it.

        While the answer is pending, inbound host requests are dispatched to
        :attr:`on_host_request`. A dispose request received this way terminates
        the process, so this method may not return.

        :param op: Operation name of the request.
        :param payload: Operation payload, empty when omitted.
        :returns: The ``result`` member of the host reply.
        :rtype: Any
        :raises HostUnavailable: If the host closes the channel.
        :raises PluginCallError: If the host replies with ``ok: false``.
        """
        request_id = self._next_id
        self._next_id += 1
        self.send({"v": PROTOCOL_VERSION, "id": request_id, "op": op, "payload": payload or {}})

        while True:
            frame = self.read_frame()
            if frame is None:
                raise HostUnavailable("host closed the channel")
            if frame.get("id") == request_id and ("ok" in frame or "error" in frame):
                if frame.get("ok"):
                    return frame.get("result")
                raise PluginCallError(
                    (frame.get("error") or {}).get("message", "host rejected the request")
                )
            if "op" in frame:
                self._dispatch_host_request(frame)

    def respond(
        self,
        request_id: Any,
        ok: bool,
        result: Any = None,
        error: dict[str, Any] | None = None,
    ) -> None:
        """Answer one host request.

        :param request_id: Id of the host frame that is being answered.
        :param ok: Whether the operation succeeded.
        :param result: Result payload, included when ``ok`` is true.
        :param error: Error object, included when ``ok`` is false. A generic
            message is used when the caller supplies none.
        """
        frame: dict[str, Any] = {"v": PROTOCOL_VERSION, "id": request_id, "ok": ok}
        if ok:
            frame["result"] = result
        else:
            frame["error"] = error or {"message": "unknown error"}
        self.send(frame)

    def _dispatch_host_request(self, frame: dict[str, Any]) -> None:
        handler = self.on_host_request
        if handler is None:
            self.respond(frame.get("id"), False, error={"message": f"unsupported host op {frame.get('op')}"})
            return
        handler(frame)
