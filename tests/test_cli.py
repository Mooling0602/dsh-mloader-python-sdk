"""The dsh-mloader-sdk command line entry point."""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

import dsh_mloader_sdk
from dsh_mloader_sdk import cli, packaging

ENVIRONMENT = {
    "python": "/fake/python",
    "venv": "/fake/venv",
    "mode": "none",
    "dependencies": [],
    "requiresPython": None,
    "warnings": [],
}


def make_plugin(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "mloader.plugin.json").write_text(
        json.dumps({
            "id": "demo",
            "name": "Demo",
            "version": "1.0.0",
            "backend": "python",
            "entry": "plugin.py",
        }),
        encoding="utf-8",
    )
    (root / "plugin.py").write_text("pass\n", encoding="utf-8")
    return root


class CliTest(unittest.TestCase):
    """Command parsing, JSON output and the installed profile default."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.profile = self.root / "profile"
        self.plugin = make_plugin(self.root / "source")
        self.archive = self.root / "demo.mloader.pack"

    def test_active_profile_uses_the_running_environment(self):
        venv = self.profile / "mloader" / ".venv"
        venv.mkdir(parents=True)
        with mock.patch.object(cli.sys, "prefix", str(venv)), \
             mock.patch.object(cli.sys, "base_prefix", "/usr"):
            self.assertEqual(cli._active_profile(), str(self.profile))

    def test_active_profile_uses_virtual_env_outside_a_venv(self):
        venv = self.profile / "mloader" / ".venv"
        venv.mkdir(parents=True)
        with mock.patch.object(cli.sys, "prefix", "/usr"), \
             mock.patch.object(cli.sys, "base_prefix", "/usr"), \
             mock.patch.dict(cli.os.environ, {"VIRTUAL_ENV": str(venv)}, clear=False):
            self.assertEqual(cli._active_profile(), str(self.profile))

    def test_help_uses_the_console_name(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            cli.main(["--help"])
        self.assertEqual(raised.exception.code, 0)
        self.assertTrue(output.getvalue().startswith("usage: dsh-mloader-sdk"))

    def test_version(self):
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit) as raised:
            cli.main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), dsh_mloader_sdk.__version__)

    def test_pack_writes_json(self):
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main(["pack", str(self.plugin), "-o", str(self.archive), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertEqual(payload["id"], "demo")
        self.assertTrue(self.archive.is_file())

    def test_install_writes_the_plugin_and_json(self):
        packaging.pack(str(self.plugin), output=str(self.archive))
        output = io.StringIO()
        with mock.patch.object(cli.packaging, "sync_plugin_environment", return_value=dict(ENVIRONMENT)), \
             redirect_stdout(output):
            code = cli.main(["install", str(self.archive), "--profile", str(self.profile), "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue().strip().splitlines()[-1])
        self.assertEqual(payload["id"], "demo")
        self.assertTrue((self.profile / "mloader" / "plugins" / "demo" / "plugin.py").is_file())

    def test_remove_moves_the_plugin_to_the_trash(self):
        packaging.pack(str(self.plugin), output=str(self.archive))
        with mock.patch.object(cli.packaging, "sync_plugin_environment", return_value=dict(ENVIRONMENT)):
            packaging.install(str(self.archive), profile_dir=str(self.profile))
        trash = self.root / "trash"
        trash.mkdir()

        def fake_trash(path):
            destination = trash / Path(path).name
            shutil.move(path, destination)
            return str(destination)

        output = io.StringIO()
        with mock.patch.object(cli.packaging, "move_to_trash", side_effect=fake_trash), \
             redirect_stdout(output):
            code = cli.main(["remove", "demo", "--profile", str(self.profile), "--yes", "--json"])
        self.assertEqual(code, 0)
        self.assertFalse((self.profile / "mloader" / "plugins" / "demo").exists())
        self.assertTrue((trash / "demo").is_dir())

    def test_a_missing_package_returns_one(self):
        error = io.StringIO()
        with redirect_stderr(error):
            code = cli.main([
                "install",
                str(self.root / "missing.pack"),
                "--profile",
                str(self.profile),
                "--json",
            ])
        self.assertEqual(code, 1)
        self.assertIn("error:", error.getvalue())
