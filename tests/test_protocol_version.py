"""The protocol version check on frames read from the host."""

from __future__ import annotations

import io
import json
import unittest

from dsh_mloader_sdk.channel import Channel
from dsh_mloader_sdk.version import PROTOCOL_VERSION


def make_channel(lines):
    """Build a channel over the given host lines and a capture buffer."""

    stdin = io.StringIO("".join(json.dumps(line) + "\n" for line in lines))
    stdout = io.StringIO()
    diagnostics = io.StringIO()
    return Channel(stdin, stdout, diagnostics), stdout, diagnostics


class ProtocolVersionTest(unittest.TestCase):
    """A frame is honoured only when it carries the version this runtime speaks."""

    def test_a_matching_frame_is_returned(self):
        channel, _stdout, _diagnostics = make_channel([
            {"v": PROTOCOL_VERSION, "id": 1, "op": "boot", "payload": {}},
        ])
        frame = channel.read_frame()
        self.assertEqual(frame["op"], "boot")

    def test_a_frame_from_another_version_is_skipped(self):
        channel, _stdout, diagnostics = make_channel([
            {"v": PROTOCOL_VERSION + 1, "id": 1, "op": "boot", "payload": {}},
            {"v": PROTOCOL_VERSION, "id": 2, "op": "apply", "payload": {}},
        ])
        frame = channel.read_frame()
        self.assertEqual(frame["op"], "apply")
        self.assertIn("protocol version", diagnostics.getvalue())

    def test_a_frame_without_a_version_is_skipped(self):
        channel, _stdout, diagnostics = make_channel([
            {"id": 1, "op": "boot", "payload": {}},
            {"v": PROTOCOL_VERSION, "id": 2, "op": "apply", "payload": {}},
        ])
        frame = channel.read_frame()
        self.assertEqual(frame["op"], "apply")
        self.assertIn("None", diagnostics.getvalue())

    def test_an_unsupported_frame_followed_by_eof_ends_the_channel(self):
        channel, _stdout, _diagnostics = make_channel([
            {"v": PROTOCOL_VERSION + 1, "id": 1, "op": "boot"},
        ])
        self.assertIsNone(channel.read_frame())
        self.assertTrue(channel.closed)

    def test_an_unsupported_request_draws_no_write(self):
        channel, stdout, _diagnostics = make_channel([
            {"v": PROTOCOL_VERSION + 1, "id": 1, "op": "boot", "payload": {}},
        ])
        self.assertIsNone(channel.read_frame())
        self.assertEqual(stdout.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
