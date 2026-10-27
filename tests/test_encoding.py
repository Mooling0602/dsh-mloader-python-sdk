# SPDX-License-Identifier: MIT
"""Encoding tests for the process entry point.

The loader encodes its requests and decodes both output streams as UTF-8, so the
plugin process must use the same codec no matter what its own environment says.
An inherited ```PYTHONIOENCODING```, or an ASCII locale with the UTF-8 mode
disabled, would otherwise select another codec and the first non-ASCII character
would raise inside a read or a write.

The subprocess tests pin ```PYTHONIOENCODING``` to ASCII, so the result does not
depend on the environment that runs the tests.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import textwrap
import unittest

from support import LAUNCH, LaunchPluginTestCase, ProtocolHost

ASCII_ENV = {"PYTHONIOENCODING": "ascii"}
"""Environment that would force the strict ASCII codec without the fix."""

ECHO_PLUGIN = """
import sys

from dsh_mloader_sdk import plugin


@plugin
def apply(ctx):
    @ctx.tools.register(name="echo_text", description="Echo the text back.")
    def echo_text(args):
        return {"text": args["text"]}

    @ctx.tools.register(name="write_diagnostic", description="Write a non-ASCII diagnostic.")
    def write_diagnostic(args):
        print("\u4e2d\u6587\u8bca\u65ad", file=sys.stderr)
        return {"ok": True}

    @ctx.tools.register(name="echo_after_diagnostic", description="Echo a fixed text.")
    def echo_after_diagnostic(args):
        return {"text": "still here"}
"""
"""Plugin whose tool results and diagnostics both carry non-ASCII characters."""


def load_entry_point():
    """Import the entry point by path without starting the runtime.

    :returns: The imported entry point module.
    :rtype: types.ModuleType
    """
    spec = importlib.util.spec_from_file_location("sdk_launch_under_test", LAUNCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StreamNormalizationTest(unittest.TestCase):
    """The entry point reconfigures the three standard streams before it runs."""

    def test_the_entry_point_reconfigures_all_three_streams_to_utf8(self):
        module = load_entry_point()
        seen = []

        class Recorder:
            def __init__(self, label):
                self.label = label

            def reconfigure(self, **kwargs):
                seen.append((self.label, kwargs))

        originals = module.sys.stdin, module.sys.stdout, module.sys.stderr
        module.sys.stdin = Recorder("stdin")
        module.sys.stdout = Recorder("stdout")
        module.sys.stderr = Recorder("stderr")
        try:
            module._use_utf8_standard_streams()
        finally:
            module.sys.stdin, module.sys.stdout, module.sys.stderr = originals

        self.assertEqual([label for label, _ in seen], ["stdin", "stdout", "stderr"])
        for _, kwargs in seen:
            self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(seen[0][1]["errors"], "replace")
        self.assertEqual(seen[1][1]["errors"], "backslashreplace")
        self.assertEqual(seen[2][1]["errors"], "backslashreplace")

    def test_a_stream_without_reconfigure_is_left_alone(self):
        module = load_entry_point()
        originals = module.sys.stdin, module.sys.stdout, module.sys.stderr
        module.sys.stdin = module.sys.stdout = module.sys.stderr = object()
        try:
            module._use_utf8_standard_streams()
        finally:
            module.sys.stdin, module.sys.stdout, module.sys.stderr = originals


class EncodingTest(LaunchPluginTestCase):
    """Tests that the plugin process speaks UTF-8 regardless of its environment."""

    def launch_plugin_in(self, environment, source=ECHO_PLUGIN):
        """Start a plugin subprocess with extra environment variables.

        :param environment: Environment variables to add for the subprocess.
        :param source: Plugin source text, dedented before it is written.
        :returns: The host connected to the started plugin process.
        :rtype: ProtocolHost
        """
        path = self.work_dir / "plugin.py"
        path.write_text(textwrap.dedent(source).lstrip("\n"), encoding="utf-8")
        env = dict(os.environ)
        env.update(environment)
        proc = subprocess.Popen(
            [sys.executable, "-u", str(LAUNCH), str(path)],
            cwd=str(self.work_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            env=env,
        )
        host = ProtocolHost(proc)
        self._hosts.append(host)
        return host

    def boot(self, host):
        """Walk the plugin through boot and apply.

        :param host: The host connected to the plugin process.
        :rtype: None
        """
        ready = host.request(1, "boot", {"pluginId": "py-encoding"})
        self.assertIs(ready["ok"], True, ready)
        applied = host.request(2, "apply", {"inject": ["tools"]})
        self.assertIs(applied["ok"], True, applied)

    def test_a_non_ascii_value_crosses_the_boundary_both_ways(self):
        host = self.launch_plugin_in(ASCII_ENV)
        self.boot(host)
        reply = host.request(3, "tool.call", {"tool": "echo_text", "args": {"text": "\u4e2d\u6587"}})
        self.assertIs(reply["ok"], True, reply)
        self.assertEqual(reply["result"], {"text": "\u4e2d\u6587"})

    def test_a_non_ascii_diagnostic_does_not_break_the_channel(self):
        host = self.launch_plugin_in(ASCII_ENV)
        self.boot(host)
        noisy = host.request(3, "tool.call", {"tool": "write_diagnostic", "args": {}})
        self.assertIs(noisy["ok"], True, noisy)
        after = host.request(4, "tool.call", {"tool": "echo_after_diagnostic", "args": {}})
        self.assertIs(after["ok"], True, after)
        self.assertEqual(after["result"], {"text": "still here"})


if __name__ == "__main__":
    unittest.main()
