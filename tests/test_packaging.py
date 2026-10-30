"""Packing, installing, updating and removing Python plugin packages."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from dsh_mloader_sdk import packaging

ENVIRONMENT = {
    "python": "/fake/python",
    "venv": "/fake/venv",
    "mode": "none",
    "dependencies": [],
    "requiresPython": None,
    "warnings": [],
}


def make_plugin(root, *, backend="python", requirements="pip>=20\n", pyproject=True):
    """Create a minimal plugin directory and return its path."""

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / "mloader.plugin.json").write_text(
        json.dumps({
            "id": "demo",
            "name": "Demo",
            "version": "1.0.0",
            "backend": backend,
            "entry": "plugin.py",
        }),
        encoding="utf-8",
    )
    (root / "plugin.py").write_text("from dsh_mloader_sdk import plugin\n", encoding="utf-8")
    module = root / "demo_module"
    module.mkdir()
    (module / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "stale.pyc").write_text("stale", encoding="utf-8")
    (root / ".venv").mkdir()
    (root / ".venv" / "marker").write_text("venv", encoding="utf-8")
    if pyproject:
        (root / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "1.0.0"\ndependencies = []\n',
            encoding="utf-8",
        )
    if requirements is not None:
        (root / "requirements.txt").write_text(requirements, encoding="utf-8")
    return root


class PackagingTestCase(unittest.TestCase):
    """Base case with an isolated profile and plugin source tree."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.profile = self.root / "profile"
        self.source = make_plugin(self.root / "source", requirements="pip>=20\n")
        self.archive = self.root / "demo.mloader.pack"

    def install(self, archive=None, *, force=False):
        target = self.archive if archive is None else archive
        with mock.patch.object(packaging, "sync_plugin_environment", return_value=dict(ENVIRONMENT)):
            return packaging.install(str(target), profile_dir=str(self.profile), force=force)

    def target(self):
        return self.profile / "mloader" / "plugins" / "demo"

    def test_pack_writes_manifest_entry_and_checksum_index(self):
        result = packaging.pack(str(self.source), output=str(self.archive))
        self.assertEqual(result["id"], "demo")
        with zipfile.ZipFile(self.archive) as archive:
            names = set(archive.namelist())
            index = json.loads(archive.read(".mloader.index.json"))
        self.assertIn("mloader.plugin.json", names)
        self.assertIn("plugin.py", names)
        self.assertIn("demo_module/__init__.py", names)
        self.assertNotIn("__pycache__/stale.pyc", names)
        self.assertFalse(any(name.startswith(".venv/") for name in names))
        self.assertEqual(index["id"], "demo")
        self.assertIn("plugin.py", index["fileMd5"])

    def test_pack_rejects_a_missing_manifest(self):
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(packaging.PackageError):
            packaging.pack(str(empty), output=str(self.archive))

    def test_pack_rejects_a_wasm_manifest(self):
        wasm = make_plugin(self.root / "wasm", backend="wasm")
        with self.assertRaises(packaging.PackageError):
            packaging.pack(str(wasm), output=str(self.archive))

    def test_install_extracts_merges_and_writes_the_index(self):
        packaging.pack(str(self.source), output=str(self.archive))
        result = self.install()
        target = self.target()
        self.assertFalse(result["updated"])
        self.assertTrue((target / "mloader.plugin.json").is_file())
        self.assertTrue((target / "demo_module" / "__init__.py").is_file())
        self.assertFalse((target / "requirements.txt").exists())
        self.assertIn('"pip>=20"', (target / "pyproject.toml").read_text(encoding="utf-8"))
        index = json.loads((target / ".mloader.index.json").read_text(encoding="utf-8"))
        self.assertEqual(index["schemaVersion"], 1)
        self.assertEqual(index["id"], "demo")
        self.assertEqual(index["packageMd5"], result["packageMd5"])
        self.assertIn("plugin.py", index["fileMd5"])
        self.assertNotIn("requirements.txt", index["fileMd5"])

    def test_install_refuses_to_reinstall_the_same_archive(self):
        packaging.pack(str(self.source), output=str(self.archive))
        self.install()
        with self.assertRaises(packaging.PackageError) as raised:
            self.install()
        self.assertIn("already installed", str(raised.exception))

    def test_install_keeps_requirements_only_packages(self):
        source = make_plugin(self.root / "legacy", pyproject=False, requirements="pip>=20\n")
        archive = self.root / "legacy.mloader.pack"
        packaging.pack(str(source), output=str(archive))
        self.install(archive)
        target = self.profile / "mloader" / "plugins" / "demo"
        self.assertTrue((target / "requirements.txt").is_file())
        self.assertFalse((target / "pyproject.toml").exists())

    def test_update_moves_the_previous_directory_to_the_trash(self):
        packaging.pack(str(self.source), output=str(self.archive))
        self.install()
        (self.source / "plugin.py").write_text("VALUE = 2\n", encoding="utf-8")
        (self.source / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
        updated = self.root / "demo-2.mloader.pack"
        packaging.pack(str(self.source), output=str(updated))
        trash = self.root / "trash"
        trash.mkdir()
        moved = []

        def fake_trash(path):
            destination = trash / Path(path).name
            shutil.move(path, destination)
            moved.append(destination)
            return str(destination)

        with mock.patch.object(packaging, "sync_plugin_environment", return_value=dict(ENVIRONMENT)), \
             mock.patch.object(packaging, "move_to_trash", side_effect=fake_trash):
            result = packaging.install(str(updated), profile_dir=str(self.profile))
        self.assertTrue(result["updated"])
        self.assertTrue((self.target() / "extra.py").is_file())
        self.assertEqual((self.target() / "plugin.py").read_text(encoding="utf-8"), "VALUE = 2\n")
        self.assertEqual(len(moved), 1)

    def test_update_refuses_an_edited_installed_file_until_force(self):
        packaging.pack(str(self.source), output=str(self.archive))
        self.install()
        (self.target() / "plugin.py").write_text("# edited after install\n", encoding="utf-8")
        (self.source / "README.md").write_text("readme\n", encoding="utf-8")
        updated = self.root / "demo-2.mloader.pack"
        packaging.pack(str(self.source), output=str(updated))
        with self.assertRaises(packaging.PackageError) as raised:
            self.install(updated)
        self.assertIn("changed after installation", str(raised.exception))
        trash = self.root / "trash"
        trash.mkdir()

        def fake_trash(path):
            destination = trash / Path(path).name
            shutil.move(path, destination)
            return str(destination)

        with mock.patch.object(packaging, "move_to_trash", side_effect=fake_trash):
            result = self.install(updated, force=True)
        self.assertTrue(result["updated"])
        self.assertNotIn("edited after install", (self.target() / "plugin.py").read_text(encoding="utf-8"))

    def test_path_traversal_is_rejected(self):
        unsafe = self.root / "unsafe.pack"
        with zipfile.ZipFile(unsafe, "w") as archive:
            archive.writestr("../escape.txt", "x")
        with self.assertRaises(packaging.PackageError) as raised:
            packaging.install(str(unsafe), profile_dir=str(self.profile))
        self.assertIn("not a safe relative path", str(raised.exception))
        self.assertFalse((self.root / "escape.txt").exists())
        self.assertFalse(self.target().exists())

    def test_install_rejects_a_package_without_a_manifest(self):
        missing = self.root / "missing.pack"
        with zipfile.ZipFile(missing, "w") as archive:
            archive.writestr("plugin.py", "pass\n")
        with self.assertRaises(packaging.PackageError):
            packaging.install(str(missing), profile_dir=str(self.profile))

    def test_remove_requires_confirmation_and_moves_to_the_trash(self):
        packaging.pack(str(self.source), output=str(self.archive))
        self.install()
        with self.assertRaises(packaging.PackageError):
            packaging.remove("demo", profile_dir=str(self.profile))
        trash = self.root / "trash"
        trash.mkdir()

        def fake_trash(path):
            destination = trash / Path(path).name
            shutil.move(path, destination)
            return str(destination)

        with mock.patch.object(packaging, "move_to_trash", side_effect=fake_trash):
            result = packaging.remove("demo", profile_dir=str(self.profile), force=True)
        self.assertFalse(self.target().exists())
        self.assertTrue(Path(result["trash"]).exists())
