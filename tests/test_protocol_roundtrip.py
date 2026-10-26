"""End to end protocol round trips against a real plugin subprocess."""

import subprocess
import sys

from support import LAUNCH, LaunchPluginTestCase

STYLE_A = """
from dsh_mloader_sdk import plugin

@plugin
def apply(ctx):
    ctx.logger.info("applied")

    @ctx.tools.register(name="greet", description="Greet one person.")
    def greet(args):
        return {"text": "hello " + args["who"]}

    @ctx.tools.register(name="explode", description="Raise on purpose.")
    def explode(args):
        raise ValueError("boom")

    @ctx.on("tools/execute")
    def observe(payload):
        ctx.logger.info("observed " + payload.get("name", "?"))

    ctx.effect.register("first", lambda: ctx.logger.info("cleanup-first"))
    ctx.effect.register("second", lambda: ctx.logger.info("cleanup-second"))
"""

STYLE_B = """
from dsh_mloader_sdk import tool

@tool(name="add", description="Add two numbers.")
def add(args):
    return {"sum": args["a"] + args["b"]}

TOOLS = [
    {
        "name": "echo",
        "description": "Echo the argument object.",
        "parameters": {"type": "object", "properties": {}},
        "handler": lambda args: args,
    },
]
"""


class ProtocolRoundtripTest(LaunchPluginTestCase):
    """The frames a real plugin process exchanges with a scripted host."""

    def test_style_a_round_trip_over_stdin_and_stdout(self):
        host = self.launch_plugin(STYLE_A)

        ready = host.request(1, "boot", {"pluginId": "py-style-a", "manifest": {"id": "py-style-a"}})
        self.assertIs(ready["ok"], True)
        self.assertEqual(ready["result"]["pluginId"], "py-style-a")
        self.assertEqual(ready["result"]["python"].count("."), 2)

        applied = host.request(2, "apply", {"inject": ["tools"]})
        self.assertIs(applied["ok"], True)
        self.assertEqual(
            applied["result"], {"tools": ["explode", "greet"], "events": ["tools/execute"]}
        )
        self.assertEqual(
            [frame["op"] for frame in host.inbound],
            [
                "tools.register",
                "tools.register",
                "events.on",
                "effect.register",
                "effect.register",
            ],
        )
        greet_spec = host.inbound[0]["payload"]["tool"]
        self.assertEqual(greet_spec["name"], "greet")
        self.assertEqual(
            greet_spec["parameters"],
            {"type": "object", "properties": {}, "additionalProperties": False},
        )
        self.assertEqual(host.notifications[0]["payload"]["message"], "applied")

        greet = host.request(3, "tool.call", {"tool": "greet", "args": {"who": "world"}})
        self.assertEqual(greet, {"v": 1, "id": 3, "ok": True, "result": {"text": "hello world"}})

        exploded = host.request(4, "tool.call", {"tool": "explode", "args": {}})
        self.assertIs(exploded["ok"], False)
        self.assertEqual(exploded["error"]["message"], "boom")

        unknown = host.request(5, "tool.call", {"tool": "missing", "args": {}})
        self.assertIs(unknown["ok"], False)
        self.assertIn("missing", unknown["error"]["message"])

        host.send(
            {
                "v": 1,
                "id": 6,
                "op": "event",
                "payload": {"event": "tools/execute", "args": [{"name": "bash"}]},
            }
        )
        notification = host.read()
        self.assertIs(notification["notification"], True)
        self.assertEqual(notification["payload"]["message"], "observed bash")
        self.assertEqual(host.read(), {"v": 1, "id": 6, "ok": True, "result": None})

        host.send({"v": 1, "id": 7, "op": "dispose", "payload": {"reason": "test"}})
        disposed = host.read_until_reply(7)
        self.assertEqual(disposed, {"v": 1, "id": 7, "ok": True, "result": {"disposed": True}})
        cleanup_logs = [
            frame["payload"]["message"]
            for frame in host.notifications
            if frame["payload"]["message"].startswith("cleanup-")
        ]
        self.assertEqual(cleanup_logs, ["cleanup-second", "cleanup-first"])

        code, stderr = host.wait_exit()
        self.assertEqual(code, 0, stderr)

    def test_style_b_declared_tools_are_registered_and_callable(self):
        host = self.launch_plugin(STYLE_B)

        ready = host.request(1, "boot", {"pluginId": "py-style-b"})
        self.assertIs(ready["ok"], True)

        applied = host.request(2, "apply", {"inject": []})
        self.assertEqual(applied["result"], {"tools": ["add", "echo"], "events": []})
        self.assertEqual([frame["op"] for frame in host.inbound], ["tools.register", "tools.register"])

        added = host.request(3, "tool.call", {"tool": "add", "args": {"a": 2, "b": 3}})
        self.assertEqual(added["result"], {"sum": 5})

        echoed = host.request(4, "tool.call", {"tool": "echo", "args": {"x": 1}})
        self.assertEqual(echoed["result"], {"x": 1})

        disposed = host.request(5, "dispose", {})
        self.assertEqual(disposed["result"], {"disposed": True})

        code, stderr = host.wait_exit()
        self.assertEqual(code, 0, stderr)

    def test_a_module_level_apply_function_is_used_without_the_decorator(self):
        host = self.launch_plugin(
            """
def apply(ctx):
    @ctx.tools.register(name="plain", description="Plain apply.")
    def plain(args):
        return {"ok": True}
"""
        )

        self.assertIs(host.request(1, "boot", {"pluginId": "py-plain-apply"})["ok"], True)
        applied = host.request(2, "apply", {"inject": []})
        self.assertEqual(applied["result"], {"tools": ["plain"], "events": []})
        self.assertEqual(
            host.request(3, "tool.call", {"tool": "plain", "args": {}})["result"],
            {"ok": True},
        )
        self.assertEqual(host.request(4, "dispose", {})["result"], {"disposed": True})

        code, stderr = host.wait_exit()
        self.assertEqual(code, 0, stderr)

    def test_a_first_frame_that_is_not_boot_is_rejected(self):
        host = self.launch_plugin("")

        reply = host.request(1, "apply", {"inject": []})
        self.assertEqual(
            reply, {"v": 1, "id": 1, "ok": False, "error": {"message": "expected boot"}}
        )

        code, _ = host.wait_exit()
        self.assertEqual(code, 1)

    def test_a_second_frame_that_is_not_apply_is_rejected(self):
        host = self.launch_plugin("")

        self.assertIs(host.request(1, "boot", {"pluginId": "py-no-apply"})["ok"], True)
        reply = host.request(2, "tool.call", {"tool": "anything", "args": {}})
        self.assertEqual(
            reply, {"v": 1, "id": 2, "ok": False, "error": {"message": "expected apply"}}
        )

        code, _ = host.wait_exit()
        self.assertEqual(code, 1)

    def test_event_handlers_receive_the_declared_arity(self):
        host = self.launch_plugin(
            """
from dsh_mloader_sdk import plugin

@plugin
def apply(ctx):
    @ctx.on("agent/status")
    def zero():
        return "zero"

    @ctx.on("tools/execute")
    def one(payload):
        return payload["n"]

    @ctx.on("tools/change")
    def two(first, second):
        return first + second
"""
        )

        self.assertIs(host.request(1, "boot", {"pluginId": "py-arity"})["ok"], True)
        applied = host.request(2, "apply", {"inject": []})
        self.assertEqual(
            applied["result"]["events"], ["agent/status", "tools/change", "tools/execute"]
        )
        self.assertEqual(
            host.request(3, "event", {"event": "agent/status", "args": []}),
            {
                "v": 1,
                "id": 3,
                "ok": True,
                "result": "zero",
            },
        )
        self.assertEqual(
            host.request(4, "event", {"event": "tools/execute", "args": [{"n": 7}]})["result"], 7
        )
        self.assertEqual(
            host.request(5, "event", {"event": "tools/change", "args": ["a", "b"]})["result"], "ab"
        )

        self.assertEqual(host.request(6, "dispose", {})["result"], {"disposed": True})
        code, stderr = host.wait_exit()
        self.assertEqual(code, 0, stderr)

    def test_an_event_handler_error_is_reported_and_the_process_survives(self):
        host = self.launch_plugin(
            """
from dsh_mloader_sdk import plugin

@plugin
def apply(ctx):
    @ctx.tools.register(name="ping", description="Ping.")
    def ping(args):
        return {"pong": True}

    @ctx.on("tools/execute")
    def fail(payload):
        raise RuntimeError("handler exploded")
"""
        )

        self.assertIs(host.request(1, "boot", {"pluginId": "py-handler-error"})["ok"], True)
        self.assertIs(host.request(2, "apply", {"inject": []})["ok"], True)

        failed = host.request(3, "event", {"event": "tools/execute", "args": [{}]})
        self.assertIs(failed["ok"], False)
        self.assertEqual(failed["error"]["message"], "handler exploded")

        self.assertEqual(
            host.request(4, "tool.call", {"tool": "ping", "args": {}})["result"], {"pong": True}
        )

        self.assertEqual(host.request(5, "dispose", {})["result"], {"disposed": True})
        code, stderr = host.wait_exit()
        self.assertEqual(code, 0, stderr)

    def test_import_failure_is_reported_in_the_boot_reply(self):
        host = self.launch_plugin("import a_module_that_does_not_exist\n")

        reply = host.request(1, "boot", {"pluginId": "py-broken-import"})
        self.assertIs(reply["ok"], False)
        self.assertTrue(reply["error"]["message"].startswith("import failed:"))

        code, stderr = host.wait_exit()
        self.assertEqual(code, 1)
        self.assertIn("ModuleNotFoundError", stderr)

    def test_apply_failure_is_reported_and_the_process_exits(self):
        host = self.launch_plugin(
            """
from dsh_mloader_sdk import plugin

@plugin
def apply(ctx):
    raise RuntimeError("apply exploded")
"""
        )

        self.assertIs(host.request(1, "boot", {"pluginId": "py-broken-apply"})["ok"], True)
        reply = host.request(2, "apply", {"inject": []})
        self.assertEqual(
            reply, {"v": 1, "id": 2, "ok": False, "error": {"message": "apply exploded"}}
        )

        code, _ = host.wait_exit()
        self.assertEqual(code, 1)


class LaunchEntryPointTest(LaunchPluginTestCase):
    """The argument checks performed by ``launch.py`` itself."""

    def test_launch_exits_two_without_a_plugin_path(self):
        result = subprocess.run(
            [sys.executable, "-u", str(LAUNCH)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("usage: launch.py", result.stderr)

    def test_launch_exits_two_for_a_missing_plugin_file(self):
        result = subprocess.run(
            [sys.executable, "-u", str(LAUNCH), str(self.work_dir / "missing.py")],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("plugin file not found", result.stderr)
