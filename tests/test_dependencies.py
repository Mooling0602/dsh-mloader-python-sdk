"""Dependency declaration parsing and merging."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dsh_mloader_sdk import dependencies

PROJECT = """[project]
name = "demo"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
  "requests>=2",
  "idna",
]

[tool.other]
value = 1
"""


class DependencyReaderTest(unittest.TestCase):
    """Reading dependencies and requires-python from a project table."""

    def test_reads_dependencies_and_requires_python(self):
        values, requires = dependencies.read_project(PROJECT)
        self.assertEqual(values, ["requests>=2", "idna"])
        self.assertEqual(requires, ">=3.10")

    def test_rejects_a_project_without_the_table(self):
        with self.assertRaises(dependencies.DependencyError):
            dependencies.read_project("[tool.other]\nvalue = 1\n")

    def test_rejects_dependencies_that_are_not_a_list(self):
        text = '[project]\nname = "demo"\ndependencies = "requests"\n'
        with self.assertRaises(dependencies.DependencyError):
            dependencies.read_project(text)

    def test_fallback_reader_matches_the_tomllib_reader(self):
        with mock.patch.object(dependencies, "_tomllib", None):
            values, requires = dependencies.read_project(PROJECT)
        self.assertEqual(values, ["requests>=2", "idna"])
        self.assertEqual(requires, ">=3.10")

    def test_fallback_reader_handles_single_line_arrays(self):
        text = '[project]\nname = "demo"\ndependencies = ["a", "b"]\n'
        with mock.patch.object(dependencies, "_tomllib", None):
            values, _requires = dependencies.read_project(text)
        self.assertEqual(values, ["a", "b"])


class RequirementTextTest(unittest.TestCase):
    """Requirement lines and project name comparison."""

    def test_requirement_name_ignores_extras_and_markers(self):
        self.assertEqual(
            dependencies.requirement_name("Requests[socks]>=2; python_version >= '3.10'"),
            "requests",
        )
        self.assertEqual(dependencies.requirement_name("foo.bar"), "foo-bar")

    def test_parse_requirements_skips_comments_options_and_blanks(self):
        text = """# a comment

-r other.txt
--index-url https://example.invalid
requests>=2  # inline
idna
"""
        values = dependencies.parse_requirements_text(text)
        self.assertEqual(values, ["requests>=2", "idna"])

    def test_read_requirements_reads_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requirements.txt"
            path.write_text("requests>=2\nidna\n", encoding="utf-8")
            self.assertEqual(dependencies.read_requirements(str(path)), ["requests>=2", "idna"])


class DependencyMergeTest(unittest.TestCase):
    """Merging requirements into an existing project table."""

    def test_only_requirements_not_named_by_pyproject_are_added(self):
        merged, added = dependencies.merge_project_dependencies(
            PROJECT,
            ["idna", "urllib3>=2", "requests[socks]"],
        )
        self.assertEqual(added, ["urllib3>=2"])
        self.assertIn('"urllib3>=2"', merged)
        self.assertIn("[tool.other]", merged)
        values, _requires = dependencies.read_project(merged)
        self.assertIn("urllib3>=2", values)
        self.assertIn("requests>=2", values)

    def test_merge_inserts_a_dependencies_key_when_absent(self):
        text = '[project]\nname = "demo"\nversion = "0.1.0"\n'
        merged, added = dependencies.merge_project_dependencies(text, ["urllib3>=2"])
        self.assertEqual(added, ["urllib3>=2"])
        values, _requires = dependencies.read_project(merged)
        self.assertEqual(values, ["urllib3>=2"])
        self.assertIn('name = "demo"', merged)

    def test_merge_returns_the_same_text_when_nothing_is_new(self):
        merged, added = dependencies.merge_project_dependencies(PROJECT, ["requests>=3"])
        self.assertEqual(added, [])
        self.assertEqual(merged, PROJECT)

    def test_fallback_merge_keeps_the_rest_of_the_file(self):
        with mock.patch.object(dependencies, "_tomllib", None):
            merged, added = dependencies.merge_project_dependencies(PROJECT, ["urllib3>=2"])
        self.assertEqual(added, ["urllib3>=2"])
        self.assertIn("[tool.other]", merged)
        with mock.patch.object(dependencies, "_tomllib", None):
            values, _requires = dependencies.read_project(merged)
        self.assertIn("urllib3>=2", values)
