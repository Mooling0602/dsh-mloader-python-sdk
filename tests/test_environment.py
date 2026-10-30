"""Profile paths, SDK fingerprints and the generated wheel."""

from __future__ import annotations

import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import dsh_mloader_sdk
from dsh_mloader_sdk import environment


def clean_environment(**values):
    """Return a patch for os.environ with only the named test values."""

    base = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DSH_PROFILE_DIR", "DSH_HOME", "DSH_PROFILE"}
    }
    base.update(values)
    return mock.patch.dict(os.environ, base, clear=True)


class ProfilePathTest(unittest.TestCase):
    """Resolution of the profile directory and the shared paths."""

    def test_explicit_profile_is_made_absolute(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                environment.resolve_profile_dir(directory),
                str(Path(directory).resolve()),
            )

    def test_dsh_profile_dir_environment_wins(self):
        with tempfile.TemporaryDirectory() as directory, clean_environment(
            HOME=directory, DSH_PROFILE_DIR=directory
        ):
            self.assertEqual(environment.resolve_profile_dir(), directory)

    def test_dsh_home_and_profile_build_the_path(self):
        with tempfile.TemporaryDirectory() as directory, clean_environment(
            HOME=directory, DSH_HOME=directory, DSH_PROFILE="web"
        ):
            self.assertEqual(
                environment.resolve_profile_dir(),
                str(Path(directory) / "profiles" / "web"),
            )

    def test_a_single_profile_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / ".dsh" / "profiles" / "web"
            profile.mkdir(parents=True)
            (profile / "package.json").write_text("{}\n", encoding="utf-8")
            (profile / "cordis.yml").write_text("- id: root\n", encoding="utf-8")
            with clean_environment(HOME=directory):
                self.assertEqual(environment.resolve_profile_dir(), str(profile))

    def test_ambiguous_profiles_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in ("one", "two"):
                profile = Path(directory) / ".dsh" / "profiles" / name
                profile.mkdir(parents=True)
                (profile / "package.json").write_text("{}\n", encoding="utf-8")
                (profile / "cordis.yml").write_text("- id: root\n", encoding="utf-8")
            with clean_environment(HOME=directory), self.assertRaises(
                environment.PythonEnvironmentError
            ):
                environment.resolve_profile_dir()

    def test_shared_paths(self):
        profile = os.path.join(os.sep, "tmp", "profile")
        mloader = os.path.join(profile, "mloader")
        venv = os.path.join(mloader, ".venv")
        script = "Scripts" if os.name == "nt" else "bin"
        executable = "python.exe" if os.name == "nt" else "python"
        self.assertEqual(environment.profile_mloader_dir(profile), mloader)
        self.assertEqual(environment.profile_plugins_dir(profile), os.path.join(mloader, "plugins"))
        self.assertEqual(environment.shared_venv_dir(profile), venv)
        self.assertEqual(
            environment.venv_python_path(venv),
            os.path.join(venv, script, executable),
        )


class SdkSourceTest(unittest.TestCase):
    """Fingerprinting and wheel assembly for the installed SDK."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.source = Path(self._tmp.name) / "dsh_mloader_sdk"
        self.source.mkdir()
        (self.source / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.source / "cli.py").write_text("def main():\n    return 0\n", encoding="utf-8")
        (self.source / "py.typed").write_text("", encoding="utf-8")
        (self.source / "__pycache__").mkdir()
        (self.source / "__pycache__" / "cached.pyc").write_text("cache", encoding="utf-8")

    def test_files_exclude_bytecode_caches(self):
        files = environment._sdk_files(str(self.source))
        self.assertIn("dsh_mloader_sdk/__init__.py", files)
        self.assertIn("dsh_mloader_sdk/cli.py", files)
        self.assertIn("dsh_mloader_sdk/py.typed", files)
        self.assertFalse(any("__pycache__" in name for name in files))
        self.assertFalse(any(name.endswith(".pyc") for name in files))

    def test_fingerprint_is_stable_and_tracks_content(self):
        first = environment._sdk_fingerprint(str(self.source))
        second = environment._sdk_fingerprint(str(self.source))
        self.assertEqual(first, second)
        (self.source / "cli.py").write_text("VALUE = 2\n", encoding="utf-8")
        self.assertNotEqual(first, environment._sdk_fingerprint(str(self.source)))

    def test_version_carries_the_fingerprint(self):
        version = environment._sdk_version(str(self.source))
        self.assertTrue(version.startswith(dsh_mloader_sdk.__version__ + "+"))
        self.assertEqual(len(version.split("+", 1)[1]), 16)

    def test_wheel_contains_the_console_entry_and_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = environment._build_sdk_wheel(
                str(self.source),
                "0.1.0+test",
                directory,
            )
            self.assertTrue(Path(path).is_file())
            with zipfile.ZipFile(path) as wheel:
                names = set(wheel.namelist())
                entry_points = wheel.read(
                    "dsh_mloader_sdk-0.1.0+test.dist-info/entry_points.txt"
                ).decode("utf-8")
                metadata = wheel.read(
                    "dsh_mloader_sdk-0.1.0+test.dist-info/METADATA"
                ).decode("utf-8")
                record = wheel.read(
                    "dsh_mloader_sdk-0.1.0+test.dist-info/RECORD"
                ).decode("utf-8")
        self.assertIn("dsh_mloader_sdk/__init__.py", names)
        self.assertIn("dsh_mloader_sdk/cli.py", names)
        self.assertIn("dsh_mloader_sdk/py.typed", names)
        self.assertIn("dsh-mloader-sdk = dsh_mloader_sdk.cli:main", entry_points)
        self.assertIn("Version: 0.1.0+test", metadata)
        self.assertTrue(record.rstrip().endswith("RECORD,,"))
