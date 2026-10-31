"""Verify runtime behavior without an installed host channel."""

import unittest

from dsh_mloader_sdk import HostUnavailable
from dsh_mloader_sdk.context import Context, _RuntimeState, state
from dsh_mloader_sdk.runtime import _handle_host_request


class RuntimeStateTest(unittest.TestCase):
    """The runtime reports unavailable state without retaining registrations."""

    def setUp(self):
        self.state = _RuntimeState()
        self.context = Context(self.state)

    def test_registrations_require_a_channel(self):
        for register in (self.context.tools.register("name", "description"), self.context.on("event")):
            with self.subTest(register=register), self.assertRaises(HostUnavailable):
                register(lambda *_: None)
        with self.assertRaises(HostUnavailable):
            self.context.effect.register("effect", lambda: None)
        self.assertEqual(self.state.cleanups, [])
        self.assertEqual(self.state.tool_handlers, {})
        self.assertEqual(self.state.event_handlers, {})

    def test_logger_drops_entries_when_the_channel_is_unavailable(self):
        self.context.logger.info("without channel")

        class BrokenChannel:
            def notify(self, *_args):
                raise ValueError("unavailable")

        self.state.channel = BrokenChannel()
        self.context.logger.info("broken channel")

    def test_host_request_requires_a_channel(self):
        previous = state.channel
        state.channel = None
        try:
            with self.assertRaises(HostUnavailable):
                _handle_host_request({"op": "event"})
        finally:
            state.channel = previous

    def test_dispose_requires_a_context(self):
        previous_channel, previous_context = state.channel, state.context
        state.channel, state.context = object(), None
        try:
            with self.assertRaises(HostUnavailable):
                _handle_host_request({"op": "dispose"})
        finally:
            state.channel, state.context = previous_channel, previous_context
