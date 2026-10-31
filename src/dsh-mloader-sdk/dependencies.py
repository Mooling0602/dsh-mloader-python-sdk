# SPDX-License-Identifier: MIT
"""Dependency declarations of a dsh-mloader Python plugin package.

A plugin declares third party packages in pyproject.toml under the
project table key dependencies. A requirements.txt may carry additional
packages; pyproject declarations win for a package both files name, and
the requirements only entries are merged into the pyproject file when a
package is installed.

tomllib is used when the interpreter provides it. Python 3.10 has no
tomllib, so this module also carries a small reader for the project
table that is limited to the two keys the package contract uses:
dependencies and requires-python. The writer works on the original text
so the rest of the file keeps its formatting.
"""

from __future__ import annotations

import json
import re
from typing import Any

try:
    import tomllib as _tomllib
except ModuleNotFoundError:  # Python 3.10
    _tomllib = None

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*")
_PROJECT_HEADER_RE = re.compile(r"(?m)^[ \t]*\[project\][ \t]*(?:#.*)?$")
_TABLE_HEADER_RE = re.compile(r"(?m)^[ \t]*\[")
_ASSIGNMENT_RE = re.compile(r"(?m)^[ \t]*{key}[ \t]*=")


class DependencyError(ValueError):
    """A dependency declaration cannot be read or merged."""


def normalize_name(name: str) -> str:
    """Normalise a project name for comparison.

    :param name: Project name as written in a requirement or pyproject
        file.
    :returns: Lowercase name with runs of dots, underscores and hyphens
        reduced to one hyphen, which is the form the package indexes
        compare.
    :rtype: str
    """
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str | None:
    """Return the project name of one requirement line.

    :param requirement: A PEP 508 requirement, for example
        requests[socks]>=2.
    :returns: Normalised project name, or None when the line does not
        start with a name.
    :rtype: str or None
    """
    match = _NAME_RE.match(requirement.strip())
    if match is None:
        return None
    return normalize_name(match.group(0))


def read_project(
    text: str, source: str = "pyproject.toml"
) -> tuple[list[str], str | None]:
    """Read the dependency declarations of a pyproject file.

    :param text: Content of the pyproject file.
    :param source: File name used in diagnostics.
    :returns: The project dependencies list and the requires-python
        value when present.
    :rtype: tuple of list and str or None
    :raises DependencyError: If the file is not valid TOML, has no
        project table, or declares dependencies that are not a list of
        strings.
    """
    if _tomllib is not None:
        try:
            data = _tomllib.loads(text)
        except Exception as exc:
            raise DependencyError(f"{source} is not valid TOML: {exc}") from exc
        project = data.get("project")
        if not isinstance(project, dict):
            raise DependencyError(
                f"{source} has no [project] table; declare dependencies under [project]"
            )
        dependencies = project.get("dependencies", [])
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) for item in dependencies
        ):
            raise DependencyError(
                f"{source}: project.dependencies must be a list of strings"
            )
        requires_python = project.get("requires-python")
        if not isinstance(requires_python, str):
            requires_python = None
        return list(dependencies), requires_python

    return _read_project_fallback(text, source)


def _read_project_fallback(text: str, source: str) -> tuple[list[str], str | None]:
    bounds = _project_bounds(text)
    if bounds is None:
        raise DependencyError(
            f"{source} has no [project] table; declare dependencies under [project]"
        )
    body_start, body_end = bounds
    value_start = _assignment_value_start(text, body_start, body_end, "dependencies")
    dependencies: list[str] = []
    if value_start is not None:
        array_start = _skip_to_array(text, value_start)
        if array_start is None:
            raise DependencyError(f"{source}: project.dependencies must be an array")
        items, _end = _parse_array(text, array_start, source)
        dependencies = items
    requires_python = None
    requires_start = _assignment_value_start(
        text, body_start, body_end, "requires-python"
    )
    if requires_start is not None:
        requires_python = _parse_string_value(text, requires_start, source)
    return dependencies, requires_python


def read_requirements(path: str, source: str | None = None) -> list[str]:
    """Read requirement lines from a requirements file.

    Option lines, blank lines and comments are skipped. The loader
    cannot apply a pip option to the shared environment, so an option
    line is ignored.

    :param path: Path of the requirements file.
    :param source: File name used in diagnostics. Defaults to the path.
    :returns: Requirement lines, in file order.
    :rtype: list of str
    :raises DependencyError: If the file cannot be decoded.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise DependencyError(f"cannot read {path}: {exc}") from exc
    return parse_requirements_text(text, source or path)


def parse_requirements_text(text: str, source: str = "requirements.txt") -> list[str]:
    """Parse requirement lines from text.

    :param text: Content of a requirements file.
    :param source: File name used in diagnostics.
    :returns: Requirement lines, in file order.
    :rtype: list of str
    """
    entries: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if raw.rstrip().endswith("\\"):
            pending += raw.rstrip()[:-1] + " "
            continue
        line = (pending + line).strip()
        pending = ""
        if not line or line.startswith("#"):
            continue
        if line.startswith("-"):
            continue
        if " #" in line:
            line = line.split(" #", 1)[0].rstrip()
        if line:
            entries.append(line)
    if pending.strip():
        entries.append(pending.strip())
    return entries


def extra_requirements(existing: list[str], entries: list[str]) -> list[str]:
    """Return the requirements a pyproject file does not already name.

    :param existing: Dependencies declared by the project dependencies
        key.
    :param entries: Requirement lines from a requirements file.
    :returns: Entries whose project name is absent from the existing
        list, in input order and without duplicates.
    :rtype: list of str
    """
    names = {requirement_name(item) for item in existing}
    names.discard(None)
    added: list[str] = []
    added_names: set[str] = set()
    for entry in entries:
        name = requirement_name(entry)
        if name is None or name in names or name in added_names:
            continue
        added.append(entry)
        added_names.add(name)
    return added


def merge_project_dependencies(
    text: str,
    extra: list[str],
    source: str = "pyproject.toml",
) -> tuple[str, list[str]]:
    """Append requirements only entries to the pyproject dependencies array.

    The rest of the file keeps its formatting. The function re-reads the
    result before returning it, so a malformed rewrite is reported
    instead of written.

    :param text: Content of the pyproject file.
    :param extra: Candidate requirement lines, normally from
        extra_requirements.
    :param source: File name used in diagnostics.
    :returns: The rewritten text and the entries that were appended.
    :rtype: tuple of str and list
    :raises DependencyError: If the file has no project table or the
        rewritten file cannot be parsed.
    """
    existing, _requires = read_project(text, source)
    added = extra_requirements(existing, extra)
    if not added:
        return text, []

    combined = existing + added
    canonical = (
        "[\n"
        + "".join(f"  {json.dumps(item, ensure_ascii=False)},\n" for item in combined)
        + "]"
    )

    bounds = _project_bounds(text)
    if bounds is None:
        raise DependencyError(f"{source} has no [project] table")
    body_start, body_end = bounds
    value_start = _assignment_value_start(text, body_start, body_end, "dependencies")
    if value_start is not None:
        array_start = _skip_to_array(text, value_start)
        if array_start is None:
            raise DependencyError(f"{source}: project.dependencies must be an array")
        _items, array_end = _parse_array(text, array_start, source)
        rewritten = text[:array_start] + canonical + text[array_end + 1 :]
    else:
        header_end = body_start
        while header_end < len(text) and text[header_end] == "\r":
            header_end += 1
        if header_end < len(text) and text[header_end] == "\n":
            header_end += 1
        block = "dependencies = " + canonical + "\n"
        rewritten = text[:header_end] + block + text[header_end:]

    verify, _requires = read_project(rewritten, source)
    present = {requirement_name(value) for value in verify}
    missing = [item for item in added if requirement_name(item) not in present]
    if missing:
        raise DependencyError(
            f"{source}: the rewritten dependencies do not contain {', '.join(missing)}"
        )
    return rewritten, added


def _project_bounds(text: str) -> tuple[int, int] | None:
    header = _PROJECT_HEADER_RE.search(text)
    if header is None:
        return None
    body_start = header.end()
    next_header = _TABLE_HEADER_RE.search(text, body_start)
    body_end = next_header.start() if next_header is not None else len(text)
    return body_start, body_end


def _assignment_value_start(
    text: str, body_start: int, body_end: int, key: str
) -> int | None:
    pattern = _ASSIGNMENT_RE.pattern.format(key=re.escape(key))
    match = re.compile(pattern).search(text, body_start, body_end)
    if match is None:
        return None
    return match.end()


def _skip_to_array(text: str, start: int) -> int | None:
    index = start
    while index < len(text):
        char = text[index]
        if char == "#":
            newline = text.find("\n", index)
            if newline == -1:
                return None
            index = newline + 1
            continue
        if char.isspace():
            index += 1
            continue
        return index if char == "[" else None
    return None


def _parse_array(text: str, start: int, source: str) -> tuple[list[str], int]:
    index = start + 1
    depth = 1
    items: list[str] = []
    buffer: list[str] = []
    while index < len(text):
        char = text[index]
        if char in ('"', "'"):
            end = _skip_string(text, index, source)
            buffer.append(text[index:end])
            index = end
            continue
        if char == "#":
            newline = text.find("\n", index)
            if newline == -1:
                raise DependencyError(f"{source}: unterminated dependencies array")
            index = newline
            continue
        if char == "[":
            depth += 1
            buffer.append(char)
            index += 1
            continue
        if char == "]":
            depth -= 1
            if depth == 0:
                _flush_item(buffer, items, source)
                return items, index
            buffer.append(char)
            index += 1
            continue
        if char == "," and depth == 1:
            _flush_item(buffer, items, source)
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    raise DependencyError(f"{source}: unterminated dependencies array")


def _flush_item(buffer: list[str], items: list[str], source: str) -> None:
    raw = "".join(buffer).strip()
    if not raw:
        return
    items.append(_unquote(raw, source))


def _skip_string(text: str, start: int, source: str) -> int:
    quote = text[start]
    triple = text[start : start + 3] == quote * 3
    index = start + 3 if triple else start + 1
    while index < len(text):
        char = text[index]
        if char == "\\" and quote == '"':
            index += 2
            continue
        if triple:
            if text[index : index + 3] == quote * 3:
                return index + 3
        elif char == quote:
            return index + 1
        index += 1
    raise DependencyError(f"{source}: unterminated string in dependencies")


def _unquote(raw: str, source: str) -> str:
    if len(raw) < 2:
        raise DependencyError(f"{source}: dependencies entries must be strings")
    if raw.startswith(('"""', "'''")):
        raise DependencyError(
            f"{source}: multi line dependency strings are not supported"
        )
    if raw[0] == '"' and raw[-1] == '"':
        try:
            return json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise DependencyError(f"{source}: cannot decode dependency {raw}") from exc
    if raw[0] == "'" and raw[-1] == "'":
        return raw[1:-1]
    raise DependencyError(f"{source}: dependencies entries must be strings")


def _parse_string_value(text: str, start: int, source: str) -> str | None:
    index = start
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text) or text[index] not in ('"', "'"):
        return None
    end = _skip_string(text, index, source)
    raw = text[index:end]
    return _unquote(raw, source)


def as_dict(dependencies: list[str], requires_python: str | None) -> dict[str, Any]:
    """Build a JSON serialisable summary of a project's declarations.

    :param dependencies: Values of the project dependencies key.
    :param requires_python: Value of requires-python when present.
    :returns: A summary suitable for a CLI result.
    :rtype: dict
    """
    return {"dependencies": dependencies, "requiresPython": requires_python}
