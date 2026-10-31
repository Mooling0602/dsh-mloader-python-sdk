"""Static types and documentation for the public context surface."""

import inspect
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dsh_mloader_sdk
from dsh_mloader_sdk.context import EffectNamespace, LoggerNamespace, ToolNamespace


class ContextTypingTest(unittest.TestCase):
    """The public context exposes discoverable method contracts."""

    def test_context_methods_have_documentation(self):
        for method in (ToolNamespace.register, LoggerNamespace.info, EffectNamespace.register):
            with self.subTest(method=method):
                self.assertTrue(inspect.getdoc(method))

    @unittest.skipUnless(shutil.which("ty"), "ty is not installed")
    def test_context_types_resolve_in_ty(self):
        source = Path(dsh_mloader_sdk.__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(source, root / "dsh_mloader_sdk", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            probe = root / "probe.py"
            probe.write_text(
                "from typing import assert_type\n"
                "from dsh_mloader_sdk import Context, ctx, plugin\n"
                "from dsh_mloader_sdk.context import ToolNamespace, EventNamespace, LoggerNamespace, EffectNamespace\n"
                "@plugin\n"
                "def apply(context: Context):\n"
                "    assert_type(context.tools, ToolNamespace)\n"
                "    assert_type(context.on, EventNamespace)\n"
                "    assert_type(context.logger, LoggerNamespace)\n"
                "    assert_type(context.effect, EffectNamespace)\n"
                "assert_type(ctx, Context)\n"
                "assert_type(ctx.tools, ToolNamespace)\n"
                "assert_type(ctx.on, EventNamespace)\n"
                "assert_type(ctx.logger, LoggerNamespace)\n"
                "assert_type(ctx.effect, EffectNamespace)\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                ["ty", "check", "--python-version", "3.11", str(probe)],
                cwd=root, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
