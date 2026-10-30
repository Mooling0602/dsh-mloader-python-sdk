# SPDX-License-Identifier: MIT
"""Pack and install dsh-mloader Python plugin packages.

A package is a zip archive with one mloader.plugin.json at its root. The shared
archive rules are specified in docs/Package.md and the Python layout in
docs/python-sdk/Package.md. This module implements the Python side:

- pack builds an archive and publishes per file checksums in
  .mloader.index.json;
- install validates the archive, extracts it under
  <profile>/mloader/plugins/<plugin-id>, merges requirements only entries into
  pyproject.toml, prepares the shared environment and writes the install index;
- remove moves an installed plugin directory to the trash.

The loader never executes plugin code while installing. Installation is done by
the command line in cli.py, not by the loader.
"""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import stat as stat_module
import tempfile
import zipfile
from datetime import datetime, timezone
from typing import Any

from . import dependencies
from .environment import (
    PythonEnvironmentError,
    profile_plugins_dir,
    resolve_profile_dir,
    sync_plugin_environment,
)
from .trash import move_to_trash

SCHEMA_VERSION = 1
"""Version of the install index format."""

MANIFEST_NAME = "mloader.plugin.json"
"""Manifest file name at the archive root."""

INDEX_NAME = ".mloader.index.json"
"""Install index file name inside the plugin directory."""

PROJECT_FILE = "pyproject.toml"
REQUIREMENTS_FILE = "requirements.txt"

MAX_ENTRIES = 4096
"""Maximum number of entries one archive may hold."""

MAX_UNPACKED_BYTES = 512 * 1024 * 1024
"""Maximum total uncompressed size of one archive."""

_PLUGIN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class PackageError(RuntimeError):
    """A plugin package cannot be built, validated, installed or removed."""


def pack(
    plugin_dir: str,
    output: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build a plugin package from one plugin directory.

    The archive carries the manifest, the entry file, the module files, and the
    optional pyproject.toml and requirements.txt. Interpreter caches, virtual
    environments and a previous install index are not packed. The archive also
    carries an .mloader.index.json with the file checksums a reader verifies
    before installation.

    :param plugin_dir: Directory holding the plugin.
    :param output: Archive path. Defaults to a sibling file named after the
        plugin id and version.
    :param force: Overwrite an existing output file.
    :returns: The archive path, its MD5, the entry count and the plugin
        identity.
    :rtype: dict
    :raises PackageError: If the directory is not a Python plugin or the output
        cannot be written.
    """
    plugin_dir = os.path.abspath(plugin_dir)
    manifest_path = os.path.join(plugin_dir, MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        raise PackageError(f"{plugin_dir} has no {MANIFEST_NAME}")
    try:
        with open(manifest_path, "rb") as handle:
            manifest = read_manifest(handle.read(), manifest_path)
    except OSError as exc:
        raise PackageError(f"cannot read {manifest_path}: {exc}") from exc

    entry = manifest["entry"]
    if not os.path.isfile(os.path.join(plugin_dir, entry)):
        raise PackageError(f"the manifest entry {entry!r} is not a file at the plugin root")

    warnings: list[str] = []
    pyproject = os.path.join(plugin_dir, PROJECT_FILE)
    requirements = os.path.join(plugin_dir, REQUIREMENTS_FILE)
    if os.path.isfile(pyproject):
        try:
            with open(pyproject, "r", encoding="utf-8") as handle:
                text = handle.read()
            _dependencies, requires_python = dependencies.read_project(text, pyproject)
        except (OSError, dependencies.DependencyError) as exc:
            raise PackageError(str(exc)) from exc
        if requires_python:
            warnings.append(
                f"pyproject.toml declares requires-python = {requires_python!r}; "
                "the declaration is ignored"
            )
    if os.path.isfile(requirements):
        try:
            dependencies.read_requirements(requirements)
        except dependencies.DependencyError as exc:
            raise PackageError(str(exc)) from exc

    if output is None:
        version = manifest.get("version") if isinstance(manifest.get("version"), str) else "0.0.0"
        safe_version = re.sub(r"[^A-Za-z0-9._-]+", "-", version).strip("-") or "0.0.0"
        output = os.path.join(
            os.path.dirname(plugin_dir),
            f"{manifest['id']}-{safe_version}.mloader.pack",
        )
    output = os.path.abspath(output)
    if os.path.exists(output) and not force:
        raise PackageError(f"{output} already exists; pass --force to overwrite it")

    files: list[tuple[str, str]] = []
    file_md5: dict[str, str] = {}
    for relative, absolute in _walk_plugin(plugin_dir, output):
        files.append((relative, absolute))
        file_md5[relative] = md5_file(absolute)

    shipped_index = {
        "schemaVersion": SCHEMA_VERSION,
        "id": manifest["id"],
        "fileMd5": file_md5,
    }
    files.sort(key=lambda item: item[0])

    try:
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(manifest_path, MANIFEST_NAME)
            archive.writestr(
                INDEX_NAME,
                json.dumps(shipped_index, indent=2, sort_keys=True) + "\n",
            )
            for relative, absolute in files:
                if relative in (MANIFEST_NAME, INDEX_NAME):
                    continue
                archive.write(absolute, relative, compress_type=zipfile.ZIP_DEFLATED)
    except OSError as exc:
        raise PackageError(f"cannot write {output}: {exc}") from exc

    return {
        "path": output,
        "md5": md5_file(output),
        "entries": len(files) + 1,
        "id": manifest["id"],
        "version": manifest.get("version", "0.0.0"),
        "warnings": warnings,
    }


def install(
    archive_path: str,
    profile_dir: str | None = None,
    plugins_dir: str | None = None,
    force: bool = False,
    python_bin: str = "python3",
) -> dict[str, Any]:
    """Install or update one plugin package.

    The archive is validated before a byte is written. The package is extracted
    into a staging directory, its requirements only entries are merged into
    pyproject.toml, the shared environment is prepared, the install index is
    written, and the staging directory replaces the installed one. A previous
    installation moves to the trash only after the new one is ready.

    :param archive_path: Package file.
    :param profile_dir: Profile directory. Resolved when omitted.
    :param plugins_dir: Override of the plugin directory. The default is
        <profile>/mloader/plugins.
    :param force: Reinstall an identical archive, or replace a directory whose
        files changed after installation.
    :param python_bin: Interpreter used to create the shared environment.
    :returns: The installed plugin id, directory, index and environment
        summary.
    :rtype: dict
    :raises PackageError: If the archive is invalid, the installation cannot
        proceed, or the environment cannot be prepared.
    """
    archive_path = os.path.abspath(archive_path)
    if not os.path.isfile(archive_path):
        raise PackageError(f"package not found: {archive_path}")
    package_md5 = md5_file(archive_path)

    profile: str | None
    if plugins_dir is not None:
        target_root = os.path.abspath(plugins_dir)
        try:
            profile = resolve_profile_dir(profile_dir)
        except PythonEnvironmentError:
            profile = None
    else:
        try:
            profile = resolve_profile_dir(profile_dir)
        except PythonEnvironmentError as exc:
            raise PackageError(str(exc)) from exc
        target_root = profile_plugins_dir(profile)

    warnings: list[str] = []
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            members = _validate_archive(archive)
            if MANIFEST_NAME not in members:
                raise PackageError(f"{archive_path} has no {MANIFEST_NAME} at its root")
            manifest = read_manifest(archive.read(members[MANIFEST_NAME]), MANIFEST_NAME)
            if manifest["entry"] not in members or members[manifest["entry"]].is_dir():
                raise PackageError(
                    f"the manifest entry {manifest['entry']!r} is not a file at the archive root"
                )

            _validate_project_files(archive, members, warnings)
            _validate_shipped_index(archive, members, manifest["id"])

            nested = _nested_manifest_prefixes(members)
            if nested:
                for prefix in sorted(nested):
                    warnings.append(
                        f"the directory {prefix!r} holds another manifest and is not extracted"
                    )

            target = os.path.join(target_root, manifest["id"])
            existing = os.path.lexists(target)
            old_index: dict[str, Any] | None = None
            if existing:
                if os.path.islink(target) or not os.path.isdir(target):
                    raise PackageError(f"{target} exists and is not a plugin directory")
                old_index = _read_index(os.path.join(target, INDEX_NAME))
                if old_index is None:
                    if not force:
                        raise PackageError(
                            f"{target} exists without an install index; pass --force to replace it"
                        )
                else:
                    version = old_index.get("schemaVersion")
                    if not isinstance(version, int) or version > SCHEMA_VERSION:
                        raise PackageError(
                            f"{target} was installed by a newer loader "
                            f"(schemaVersion={version!r}); refusing to replace it"
                        )
                    if old_index.get("packageMd5") == package_md5 and not force:
                        raise PackageError(
                            f"{archive_path} is already installed at {target}; pass --force "
                            "to reinstall it"
                        )
                    changed = _changed_files(target, old_index.get("fileMd5"))
                    if changed and not force:
                        raise PackageError(
                            f"{target} has files changed after installation "
                            f"({', '.join(changed[:5])}); pass --force to replace it"
                        )

            os.makedirs(target_root, exist_ok=True)
            # Stage outside the watched plugin root when possible, so a rescan
            # cannot discover a half installed plugin. The parent of the root is
            # on the same file system, which keeps the final replace atomic.
            staging_parent = os.path.dirname(target_root.rstrip(os.sep)) or target_root
            try:
                staging = tempfile.mkdtemp(
                    prefix=f".mloader-{manifest['id']}-",
                    dir=staging_parent,
                )
            except OSError:
                staging = tempfile.mkdtemp(
                    prefix=f".mloader-{manifest['id']}-",
                    dir=target_root,
                )
            environment: dict[str, Any] | None = None
            merged: list[str] = []
            try:
                _extract(archive, members, staging, nested)
                merged = _merge_requirements(staging)
                if not os.path.isfile(os.path.join(staging, manifest["entry"])):
                    raise PackageError(
                        f"the manifest entry {manifest['entry']!r} was not extracted"
                    )
                if profile is not None:
                    environment = sync_plugin_environment(profile, staging, python_bin)
                    warnings.extend(environment.get("warnings", []))
                else:
                    warnings.append(
                        "no profile was resolved, so the shared Python environment and "
                        "the plugin dependencies were not prepared"
                    )
                index = {
                    "schemaVersion": SCHEMA_VERSION,
                    "id": manifest["id"],
                    "installedAt": _old_installed_at(old_index),
                    "updatedAt": _now_iso(),
                    "packageFile": os.path.basename(archive_path),
                    "packageMd5": package_md5,
                    "fileMd5": _directory_md5(staging),
                }
                with open(os.path.join(staging, INDEX_NAME), "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(index, indent=2, sort_keys=True) + "\n")

                if existing:
                    trashed = move_to_trash(target)
                    try:
                        os.replace(staging, target)
                    except OSError:
                        try:
                            shutil.move(trashed, target)
                        except OSError:
                            pass
                        raise
                else:
                    os.replace(staging, target)
                staging = None
            finally:
                if staging is not None and os.path.isdir(staging):
                    shutil.rmtree(staging, ignore_errors=True)
    except zipfile.BadZipFile as exc:
        raise PackageError(f"{archive_path} is not a zip archive: {exc}") from exc
    except OSError as exc:
        raise PackageError(f"cannot install {archive_path}: {exc}") from exc

    return {
        "id": manifest["id"],
        "version": manifest.get("version", "0.0.0"),
        "directory": target,
        "updated": existing,
        "packageMd5": package_md5,
        "requirementsMerged": merged,
        "environment": environment,
        "warnings": _dedupe(warnings),
    }


def remove(
    plugin_id: str,
    profile_dir: str | None = None,
    plugins_dir: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Move one installed plugin directory to the trash.

    :param plugin_id: Plugin id, equal to the installed directory name.
    :param profile_dir: Profile directory. Resolved when omitted.
    :param plugins_dir: Override of the plugin directory.
    :param force: Confirm the removal. Without it the call refuses.
    :returns: The removed id and the trash destination.
    :rtype: dict
    :raises PackageError: If the plugin is not installed or the confirmation is
        missing.
    """
    if not _PLUGIN_ID_RE.match(plugin_id):
        raise PackageError(f"{plugin_id!r} is not a valid plugin id")
    if plugins_dir is not None:
        target_root = os.path.abspath(plugins_dir)
    else:
        try:
            target_root = profile_plugins_dir(resolve_profile_dir(profile_dir))
        except PythonEnvironmentError as exc:
            raise PackageError(str(exc)) from exc
    target = os.path.join(target_root, plugin_id)
    if not os.path.isdir(target):
        raise PackageError(f"plugin {plugin_id!r} is not installed at {target}")
    if not force:
        raise PackageError(f"pass --yes to remove {plugin_id!r} from {target}")
    try:
        destination = move_to_trash(target)
    except OSError as exc:
        raise PackageError(f"cannot remove {target}: {exc}") from exc
    return {"id": plugin_id, "directory": target, "trash": destination}


def read_manifest(data: bytes, source: str = MANIFEST_NAME) -> dict[str, Any]:
    """Validate and return one plugin manifest.

    :param data: Manifest bytes.
    :param source: Name used in diagnostics.
    :returns: The parsed manifest.
    :rtype: dict
    :raises PackageError: If the manifest is not a Python plugin manifest with a
        valid id and a root entry file.
    """
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"{source} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PackageError(f"{source} must contain a JSON object")
    plugin_id = parsed.get("id")
    if not isinstance(plugin_id, str) or not _PLUGIN_ID_RE.match(plugin_id):
        raise PackageError(
            f"{source}: id must match ^[a-z0-9][a-z0-9_-]{{0,31}}$"
        )
    if parsed.get("backend") != "python":
        raise PackageError(f"{source}: backend must be \"python\"")
    if "targets" in parsed:
        raise PackageError(f"{source}: targets is a WebAssembly package field")
    entry = parsed.get("entry")
    if not isinstance(entry, str) or not entry:
        raise PackageError(f"{source}: entry is required and must be a non-empty string")
    if "\\" in entry or posixpath.isabs(entry) or _DRIVE_RE.match(entry):
        raise PackageError(f"{source}: entry must be a relative path at the archive root")
    if posixpath.normpath(entry) != entry or "/" in entry or entry in (".", ".."):
        raise PackageError(f"{source}: entry must name a file at the archive root")
    return parsed


def md5_file(path: str) -> str:
    """Return the lowercase hexadecimal MD5 of one file.

    :param path: File path.
    :returns: MD5 digest.
    :rtype: str
    :raises OSError: If the file cannot be read.
    """
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_plugin(plugin_dir: str, output: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    skip_names = {"__pycache__", ".venv", ".git", "node_modules"}
    output = os.path.abspath(output)
    for root, directories, files in os.walk(plugin_dir):
        directories[:] = sorted(
            name for name in directories if name not in skip_names
        )
        for name in sorted(files):
            absolute = os.path.join(root, name)
            if os.path.islink(absolute):
                raise PackageError(f"{absolute} is a symbolic link; a package cannot carry one")
            if name.endswith(".pyc") or name == INDEX_NAME:
                continue
            if os.path.abspath(absolute) == output:
                continue
            relative = os.path.relpath(absolute, plugin_dir).replace(os.sep, "/")
            result.append((relative, absolute))
    return result


def _validate_archive(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    seen: set[str] = set()
    total = 0
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise PackageError(f"the archive holds more than {MAX_ENTRIES} entries")
    for info in infos:
        name = info.filename
        if not name:
            raise PackageError("the archive holds an entry with an empty name")
        if "\\" in name:
            raise PackageError(f"the archive entry {name!r} uses a backslash")
        if name.startswith("/") or _DRIVE_RE.match(name):
            raise PackageError(f"the archive entry {name!r} is absolute")
        normalized = name.rstrip("/")
        parts = normalized.split("/")
        if not normalized or any(part in ("", ".", "..") for part in parts):
            raise PackageError(f"the archive entry {name!r} is not a safe relative path")
        if posixpath.normpath(normalized) != normalized:
            raise PackageError(f"the archive entry {name!r} is not normalized")
        mode = (info.external_attr >> 16) & 0o170000
        if stat_module.S_ISLNK(mode):
            raise PackageError(f"the archive entry {name!r} is a symbolic link")
        if info.flag_bits & 0x1:
            raise PackageError(f"the archive entry {name!r} is encrypted")
        total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise PackageError(
                f"the archive expands past {MAX_UNPACKED_BYTES // (1024 * 1024)} MiB"
            )
        key = name.casefold()
        if key in seen:
            raise PackageError(f"the archive holds {name!r} more than once")
        seen.add(key)
        members[name] = info
    return members


def _nested_manifest_prefixes(members: dict[str, zipfile.ZipInfo]) -> tuple[str, ...]:
    prefixes: set[str] = set()
    for name in members:
        if name == MANIFEST_NAME:
            continue
        if name.endswith("/" + MANIFEST_NAME):
            prefixes.add(name[: -len(MANIFEST_NAME)])
    return tuple(sorted(prefixes))


def _validate_project_files(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    warnings: list[str],
) -> None:
    if PROJECT_FILE in members:
        try:
            text = archive.read(members[PROJECT_FILE]).decode("utf-8")
            _dependencies, requires_python = dependencies.read_project(text, PROJECT_FILE)
        except (UnicodeDecodeError, dependencies.DependencyError) as exc:
            raise PackageError(str(exc)) from exc
        if requires_python:
            warnings.append(
                f"pyproject.toml declares requires-python = {requires_python!r}; "
                "Python version selection is not implemented, so the declaration is ignored"
            )
    if REQUIREMENTS_FILE in members:
        try:
            text = archive.read(members[REQUIREMENTS_FILE]).decode("utf-8")
            dependencies.parse_requirements_text(text, REQUIREMENTS_FILE)
        except (UnicodeDecodeError, dependencies.DependencyError) as exc:
            raise PackageError(str(exc)) from exc


def _validate_shipped_index(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    plugin_id: str,
) -> None:
    if INDEX_NAME not in members:
        return
    try:
        payload = json.loads(archive.read(members[INDEX_NAME]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"the packaged index is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise PackageError("the packaged index must be a JSON object")
    version = payload.get("schemaVersion")
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise PackageError(f"the packaged index has an unsupported schemaVersion {version!r}")
    if payload.get("id") != plugin_id:
        raise PackageError("the packaged index id does not match the manifest id")
    file_md5 = payload.get("fileMd5")
    if not isinstance(file_md5, dict):
        raise PackageError("the packaged index has no fileMd5 object")
    for relative, expected in file_md5.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise PackageError("the packaged index fileMd5 map is malformed")
        if relative not in members:
            raise PackageError(f"the packaged index names a missing file {relative!r}")
        actual = hashlib.md5(archive.read(members[relative])).hexdigest()
        if actual != expected.lower():
            raise PackageError(f"the packaged checksum of {relative!r} does not match")


def _extract(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    staging: str,
    nested: tuple[str, ...],
) -> None:
    staging = os.path.abspath(staging)
    for name in sorted(members):
        if name == INDEX_NAME:
            continue
        if any(name.startswith(prefix) for prefix in nested):
            continue
        target = os.path.join(staging, *name.rstrip("/").split("/"))
        if not os.path.abspath(target).startswith(staging + os.sep):
            raise PackageError(f"the archive entry {name!r} escapes the plugin directory")
        if name.endswith("/"):
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with archive.open(members[name], "r") as source, open(target, "wb") as destination:
            shutil.copyfileobj(source, destination)


def _merge_requirements(staging: str) -> list[str]:
    pyproject = os.path.join(staging, PROJECT_FILE)
    requirements = os.path.join(staging, REQUIREMENTS_FILE)
    if not (os.path.isfile(pyproject) and os.path.isfile(requirements)):
        return []
    try:
        with open(pyproject, "r", encoding="utf-8") as handle:
            text = handle.read()
        entries = dependencies.read_requirements(requirements)
        rewritten, added = dependencies.merge_project_dependencies(text, entries, pyproject)
    except (OSError, dependencies.DependencyError) as exc:
        raise PackageError(str(exc)) from exc
    if added:
        with open(pyproject, "w", encoding="utf-8") as handle:
            handle.write(rewritten)
    os.unlink(requirements)
    return added


def _directory_md5(directory: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for root, _directories, files in os.walk(directory):
        for name in files:
            absolute = os.path.join(root, name)
            relative = os.path.relpath(absolute, directory).replace(os.sep, "/")
            if relative == INDEX_NAME:
                continue
            result[relative] = md5_file(absolute)
    return dict(sorted(result.items()))


def _changed_files(directory: str, file_md5: Any) -> list[str]:
    if not isinstance(file_md5, dict):
        return []
    changed: list[str] = []
    for relative, expected in file_md5.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            continue
        parts = relative.split("/")
        if not relative or relative.startswith("/") or any(part in ("", ".", "..") for part in parts):
            continue
        absolute = os.path.join(directory, *parts)
        if not os.path.isfile(absolute) or md5_file(absolute) != expected.lower():
            changed.append(relative)
    return changed


def _read_index(path: str) -> dict[str, Any] | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise PackageError(f"cannot read the install index {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PackageError(f"the install index {path} must be a JSON object")
    return payload


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _old_installed_at(index: dict[str, Any] | None) -> str:
    if index is not None:
        value = index.get("installedAt")
        if isinstance(value, str) and value:
            return value
    return _now_iso()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")  # noqa: UP017 - Python 3.10
