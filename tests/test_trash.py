"""Moving a plugin directory to a trash location."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dsh_mloader_sdk import trash


class TrashTest(unittest.TestCase):
    """The portable fallback keeps the removed directory recoverable."""

    def test_fallback_moves_the_path_under_the_temporary_trash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "plugin"
            source.mkdir()
            (source / "plugin.py").write_text("pass\n", encoding="utf-8")
            temporary = root / "tmp"
            temporary.mkdir()

            with mock.patch.object(trash, "_xdg_trash", return_value=None), \
                 mock.patch.object(trash, "_macos_trash", return_value=None), \
                 mock.patch.object(trash.tempfile, "gettempdir", return_value=str(temporary)):
                destination = trash.move_to_trash(str(source))

            self.assertTrue(Path(destination).is_dir())
            self.assertEqual((Path(destination) / "plugin.py").read_text(encoding="utf-8"), "pass\n")
            self.assertFalse(source.exists())
            self.assertEqual(Path(destination).parent, temporary / "mloader-trash")

    def test_xdg_trash_writes_a_trashinfo_record(self):
        if not hasattr(trash.os, "uname") or trash.os.uname().sysname != "Linux":
            self.skipTest("the XDG trash layout is Linux specific")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "plugin"
            source.mkdir()
            (source / "plugin.py").write_text("pass\n", encoding="utf-8")
            data = root / "data"
            with mock.patch.dict(trash.os.environ, {"XDG_DATA_HOME": str(data)}, clear=False):
                destination = trash.move_to_trash(str(source))
            self.assertEqual(Path(destination).parent, data / "Trash" / "files")
            info = data / "Trash" / "info" / (Path(destination).name + ".trashinfo")
            self.assertTrue(info.is_file())
            text = info.read_text(encoding="utf-8")
            self.assertIn("[Trash Info]", text)
            self.assertIn("Path=", text)
            self.assertIn("DeletionDate=", text)
