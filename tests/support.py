"""Shared helpers for the dsh-mloader SDK test suite.

The suite uses the standard library only, so these helpers are plain classes
and a few base test cases rather than a test runner plugin.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from dsh_mloader_sdk import HostUnavailable
from dsh_mloader_sdk.context import Context, state

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCH = REPO_ROOT / "src" / "dsh-mloader-sdk" / "launch.py"


class FakeChannel:
    """An in-process stand-in for the protocol channel.

    :param replies: Values returned by successive :meth:`request` calls. An
        exception instance is raised instead of returned.
    """

    def __init__(self, replies=None):
        self.sent = []
        self.replies = list(replies or [])
        self.closed = False

    def request(self, op, payload=None):
        self.sent.append({"op": op, "payload": payload or {}})
        if self.replies:
            reply = self.replies.pop(0)
            if isinstance(reply, BaseException):
                raise reply
            return reply
        return None

    def notify(self, op, payload):
        if self.closed:
            raise HostUnavailable("channel is closed")
        self.sent.append({"op": op, "payload": payload, "notification": True})


class ProtocolHost:
    """A scripted host that owns one plugin subprocess.

    Inbound plugin requests are acknowledged with a null result, so a test sees
    the registration frames a plugin actually sent.

    :param proc: The plugin subprocess.
    """

    def __init__(self, proc):
        self.proc = proc
        self.notifications = []
        self.inbound = []

    def send(self, frame):
        self.proc.stdin.write(json.dumps(frame, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def read(self):
        line = self.proc.stdout.readline()
        if not line:
            stderr = self.proc.stderr.read()
            raise AssertionError(f"plugin closed stdout before replying\nstderr:\n{stderr}")
        return json.loads(line)

    def read_until_reply(self, request_id):
        """Read frames until the reply to ``request_id`` arrives.

        Notifications are collected on :attr:`notifications` and inbound plugin
        requests are collected on :attr:`inbound` and acknowledged.
        """
        while True:
            frame = self.read()
            if "op" in frame and "ok" not in frame:
                if frame.get("notification") is True:
                    self.notifications.append(frame)
                    continue
                self.inbound.append(frame)
                self.send({"v": 1, "id": frame["id"], "ok": True, "result": None})
                continue
            if frame.get("id") != request_id:
                raise AssertionError(
                    f"unexpected reply id {frame.get('id')} while waiting for {request_id}: {frame}"
                )
            return frame

    def request(self, request_id, op, payload=None):
        self.send({"v": 1, "id": request_id, "op": op, "payload": payload or {}})
        return self.read_until_reply(request_id)

    def wait_exit(self, timeout=10):
        try:
            self.proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
            raise AssertionError("plugin process did not exit")
        return code, self.proc.stderr.read()


def reset_state():
    """Clear the module level runtime state between tests."""

    state.channel = None
    state.context = None
    state.apply_fn = None
    state.tool_handlers.clear()
    state.event_handlers.clear()
    state.cleanups.clear()
    state.declared_tools.clear()


class SdkTestCase(unittest.TestCase):
    """Base test case that clears the module level runtime state around a test."""

    def setUp(self):
        reset_state()

    def tearDown(self):
        reset_state()


class ContextTestCase(SdkTestCase):
    """Base test case that installs a context backed by :class:`FakeChannel`."""

    def setUp(self):
        super().setUp()
        self.channel = FakeChannel()
        state.channel = self.channel
        self.context = Context(state)
        state.context = self.context


class LaunchPluginTestCase(SdkTestCase):
    """Base test case that starts real plugin subprocesses through ``launch.py``."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.work_dir = Path(self._tmp.name)
        self._hosts = []
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(self._stop_hosts)

    def _stop_hosts(self):
        for host in self._hosts:
            if host.proc.poll() is None:
                host.proc.kill()
                host.proc.wait(timeout=5)

    def launch_plugin(self, source, *, name="plugin.py"):
        """Write a plugin file into the case directory and start it.

        :param source: Plugin source text, dedented before it is written.
        :param name: File name inside the case directory.
        :returns: The host connected to the started plugin process.
        :rtype: ProtocolHost
        """
        path = self.work_dir / name
        path.write_text(textwrap.dedent(source).lstrip("\n"), encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-u", str(LAUNCH), str(path)],
            cwd=str(self.work_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        host = ProtocolHost(proc)
        self._hosts.append(host)
        return host
