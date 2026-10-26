"""The ctx whitelist projection, its namespaces and its error types."""

import io
from contextlib import redirect_stderr

from dsh_mloader_sdk import HostUnavailable, PluginCallError, ctx
from dsh_mloader_sdk.context import state

from support import ContextTestCase


class ContextNamespacesTest(ContextTestCase):
    """The namespaces exposed on ctx and the frames they send."""

    def test_the_ctx_proxy_forwards_after_the_context_is_installed(self):
        self.assertIs(ctx.tools, self.context.tools)
        self.assertIs(ctx.logger, self.context.logger)
        self.assertIs(ctx.effect, self.context.effect)

    def test_tool_registration_sends_the_default_parameter_schema(self):
        @self.context.tools.register(name="ping", description="Ping.")
        def ping(args):
            return {"pong": True}

        frame = self.channel.sent[0]
        self.assertEqual(frame["op"], "tools.register")
        self.assertEqual(
            frame["payload"]["tool"],
            {
                "name": "ping",
                "description": "Ping.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        )
        self.assertIs(state.tool_handlers["ping"], ping)

    def test_tool_registration_forwards_the_optional_fields(self):
        parameters = {"type": "object", "properties": {"a": {"type": "number"}}}
        output = {"type": "object", "properties": {"sum": {"type": "number"}}}

        @self.context.tools.register(
            name="add",
            description="Add two numbers.",
            parameters=parameters,
            output=output,
            timeout_ms=1500,
        )
        def add(args):
            return {"sum": args["a"]}

        tool_spec = self.channel.sent[0]["payload"]["tool"]
        self.assertIs(tool_spec["parameters"], parameters)
        self.assertIs(tool_spec["output"], output)
        self.assertEqual(tool_spec["timeoutMs"], 1500)

    def test_rejected_tool_registration_raises_plugin_call_error(self):
        self.channel.replies.append(PluginCallError("tool name is already taken"))

        with self.assertRaisesRegex(PluginCallError, "tool name is already taken"):

            @self.context.tools.register(name="taken", description="Taken.")
            def taken(args):
                return {}

        self.assertNotIn("taken", state.tool_handlers)

    def test_closed_channel_raises_host_unavailable(self):
        self.channel.replies.append(HostUnavailable("host closed the channel"))

        with self.assertRaises(HostUnavailable):

            @self.context.tools.register(name="late", description="Late.")
            def late(args):
                return {}

    def test_event_subscription_registers_the_handler(self):
        @self.context.on("tools/execute")
        def on_execute(payload):
            return payload

        self.assertEqual(
            self.channel.sent[0], {"op": "events.on", "payload": {"event": "tools/execute"}}
        )
        self.assertIs(state.event_handlers["tools/execute"], on_execute)

    def test_event_subscription_through_the_on_method(self):
        @self.context.on.on("tools/result")
        def on_result(result):
            return result

        self.assertEqual(
            self.channel.sent[0], {"op": "events.on", "payload": {"event": "tools/result"}}
        )
        self.assertIs(state.event_handlers["tools/result"], on_result)

    def test_a_second_subscription_replaces_the_first_handler(self):
        @self.context.on("tools/execute")
        def first(payload):
            return None

        @self.context.on("tools/execute")
        def second(payload):
            return None

        self.assertIs(state.event_handlers["tools/execute"], second)

    def test_logger_sends_one_way_notifications(self):
        self.context.logger.info("hello", {"key": "value"})

        frame = self.channel.sent[0]
        self.assertIs(frame["notification"], True)
        self.assertEqual(frame["op"], "logger.log")
        self.assertEqual(
            frame["payload"],
            {"level": "info", "message": "hello", "data": {"key": "value"}},
        )
        self.assertEqual(self.channel.replies, [])

    def test_logger_levels_and_the_warning_alias(self):
        self.context.logger.debug("a")
        self.context.logger.info("b")
        self.context.logger.warn("c")
        self.context.logger.warning("d")
        self.context.logger.error("e")

        self.assertEqual(
            [frame["payload"]["level"] for frame in self.channel.sent],
            ["debug", "info", "warn", "warn", "error"],
        )

    def test_logger_stringifies_the_message(self):
        self.context.logger.warn(42)

        self.assertEqual(self.channel.sent[0]["payload"]["message"], "42")

    def test_logger_drops_entries_on_a_closed_channel(self):
        self.channel.closed = True

        self.context.logger.warn("this entry is lost")

        self.assertEqual(self.channel.sent, [])

    def test_effect_cleanups_run_in_last_in_first_out_order(self):
        order = []
        self.context.effect.register("first", lambda: order.append("first"))
        self.context.effect.register("second", lambda: order.append("second"))

        self.assertEqual(
            [frame["payload"]["label"] for frame in self.channel.sent], ["first", "second"]
        )

        self.context.effect.run_cleanups()
        self.assertEqual(order, ["second", "first"])

    def test_effect_registration_without_a_cleanup_only_announces_itself(self):
        self.context.effect.register("announced")

        self.assertEqual(
            self.channel.sent[0], {"op": "effect.register", "payload": {"label": "announced"}}
        )
        self.assertEqual(state.cleanups, [])

    def test_a_failing_cleanup_does_not_stop_the_remaining_cleanups(self):
        order = []

        def failing():
            raise RuntimeError("cleanup exploded")

        self.context.effect.register("failing", failing)
        self.context.effect.register("healthy", lambda: order.append("healthy"))

        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.context.effect.run_cleanups()

        self.assertEqual(order, ["healthy"])
        self.assertIn("cleanup exploded", stderr.getvalue())
