# SPDX-License-Identifier: MIT
"""The shared Python environment of one dsh profile.

Every Python plugin hosted by dsh-mloader in a profile runs in one
virtual environment, created under the profile rather than beside a
plugin:

<profile>/mloader/.venv

A plugin may declare third party PyPI dependencies in pyproject.toml.
The loader installs them with uv into that shared environment. A plugin
that ships only requirements.txt is handled with the venv module and pip
of the configured Python interpreter, and reports a warning, because uv
is the supported dependency manager.

Per plugin environments are not implemented. A plugin that needs a
conflicting dependency set has no supported isolation yet.
"""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import subprocess
import tempfile
import zipfile
from typing import Any

from .dependencies import (
    extra_requirements,
    read_project,
    read_requirements,
)
from .version import __version__

VENV_DIR_NAME = ".venv"
"""Directory name of the shared virtual environment under the profile."""

PROJECT_FILE = "pyproject.toml"
"""Name of the optional dependency declaration file."""

REQUIREMENTS_FILE = "requirements.txt"
"""Name of the optional requirements file."""

_VENV_TIMEOUT_SECONDS = 300
_INSTALL_TIMEOUT_SECONDS = 900

_SDK_DIST_NAME = "dsh-mloader-sdk"
_SDK_IMPORT_NAME = "dsh_mloader_sdk"
_SDK_SOURCE = os.path.dirname(os.path.abspath(__file__))
_CONSOLE_SCRIPT_NAME = "dsh-mloader-sdk"
_LEGACY_CONSOLE_SCRIPT_NAME = "dsh-mloader"


class PythonEnvironmentError(RuntimeError):
    """A shared Python environment cannot be resolved or prepared."""


def resolve_profile_dir(explicit: str | None = None) -> str:
    """Resolve the dsh profile directory.

    Sources are tried in this order: the explicit argument, DSH_PROFILE_DIR,
    DSH_HOME plus DSH_PROFILE, and a scan of ~/.dsh/profiles that succeeds only
    when exactly one candidate has the files an initialised profile carries.

    :param explicit: Profile directory supplied on the command line.
    :returns: Absolute profile directory.
    :rtype: str
    :raises PythonEnvironmentError: If no source identifies exactly one profile.
    """
    if isinstance(explicit, str) and explicit:
        return os.path.abspath(os.path.expanduser(explicit))

    from_env = os.environ.get("DSH_PROFILE_DIR")
    if from_env:
        return os.path.abspath(os.path.expanduser(from_env))

    home_env = os.environ.get("DSH_HOME")
    name_env = os.environ.get("DSH_PROFILE")
    if home_env and name_env:
        return os.path.join(
            os.path.abspath(os.path.expanduser(home_env)), "profiles", name_env
        )

    home = os.environ.get("HOME") or os.path.expanduser("~")
    profiles_root = os.path.join(home, ".dsh", "profiles")
    candidates = []
    try:
        entries = os.listdir(profiles_root)
    except OSError:
        entries = []
    for entry in entries:
        candidate = os.path.join(profiles_root, entry)
        if os.path.isdir(candidate) and _looks_like_profile(candidate):
            candidates.append(candidate)
    if len(candidates) == 1:
        return candidates[0]
    raise PythonEnvironmentError(
        "cannot determine the dsh profile directory; pass --profile or set "
        "DSH_PROFILE_DIR, or set DSH_HOME and DSH_PROFILE"
    )


def _looks_like_profile(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "package.json")) and os.path.isfile(
        os.path.join(path, "cordis.yml")
    )


def profile_mloader_dir(profile_dir: str) -> str:
    """Return the mloader directory of one profile.

    :param profile_dir: Absolute profile directory.
    :returns: The directory that owns the shared environment and the
        plugins.
    :rtype: str
    """
    return os.path.join(profile_dir, "mloader")


def profile_plugins_dir(profile_dir: str) -> str:
    """Return the plugin directory of one profile.

    :param profile_dir: Absolute profile directory.
    :returns: The directory discovered as the last plugin root.
    :rtype: str
    """
    return os.path.join(profile_mloader_dir(profile_dir), "plugins")


def shared_venv_dir(profile_dir: str) -> str:
    """Return the shared virtual environment directory of one profile.

    :param profile_dir: Absolute profile directory.
    :returns: The environment directory, whether or not it exists.
    :rtype: str
    """
    return os.path.join(profile_mloader_dir(profile_dir), VENV_DIR_NAME)


def venv_python_path(venv_dir: str) -> str:
    """Return the interpreter path inside a virtual environment directory.

    :param venv_dir: Virtual environment directory.
    :returns: The platform specific interpreter path.
    :rtype: str
    """
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
    return os.path.join(venv_dir, "bin", "python")


def shared_venv_python(profile_dir: str) -> str:
    """Return the shared interpreter path of one profile.

    :param profile_dir: Absolute profile directory.
    :returns: The interpreter path, whether or not it exists.
    :rtype: str
    """
    return venv_python_path(shared_venv_dir(profile_dir))


def ensure_shared_venv(
    profile_dir: str,
    python_bin: str,
    python_venv: bool = False,
) -> str:
    """Create the shared environment when it is missing and return its Python.

    uv creates the environment and seeds pip into it. A requirements
    only plugin asks for the venv module of the configured interpreter
    instead, because its dependencies are not managed by uv; that path
    is used even when uv is available. Once an environment exists it is
    reused.

    :param profile_dir: Absolute profile directory.
    :param python_bin: Interpreter used to create the environment.
    :param python_venv: Whether the venv module creates the environment
        instead of uv.
    :returns: Path of the environment interpreter.
    :rtype: str
    :raises PythonEnvironmentError: If the environment cannot be
        created.
    """
    python = shared_venv_python(profile_dir)
    if os.path.isfile(python):
        _ensure_sdk(python)
        return python

    mloader_dir = profile_mloader_dir(profile_dir)
    venv_dir = shared_venv_dir(profile_dir)
    try:
        os.makedirs(mloader_dir, exist_ok=True)
    except OSError as exc:
        raise PythonEnvironmentError(f"cannot create {mloader_dir}: {exc}") from exc

    if python_venv:
        result = _run(
            [python_bin, "-m", "venv", venv_dir],
            cwd=mloader_dir,
            timeout=_VENV_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            raise PythonEnvironmentError(
                f"python -m venv could not create {venv_dir}: {_failure_detail(result)}"
            )
        if not os.path.isfile(python):
            raise PythonEnvironmentError(
                f"the environment at {venv_dir} has no interpreter"
            )
        _ensure_sdk(python)
        return python

    uv = shutil.which("uv")
    if uv is None:
        raise PythonEnvironmentError(
            "uv is required to create the shared Python environment; install uv and "
            "make sure it is on PATH"
        )
    result = _run(
        [uv, "venv", "--seed", "--python", python_bin, venv_dir],
        cwd=mloader_dir,
        timeout=_VENV_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise PythonEnvironmentError(
            f"uv could not create {venv_dir}: {_failure_detail(result)}"
        )

    if not os.path.isfile(python):
        raise PythonEnvironmentError(
            f"the environment at {venv_dir} has no interpreter"
        )
    _ensure_sdk(python)
    return python


def _ensure_sdk(python: str) -> None:
    """Install the runtime package and its console script into an environment.

    The package is copied into the environment through a wheel built
    from the source tree, so no build backend or network access is
    needed. The wheel version carries a fingerprint of the source, which
    makes pip replace an installed copy when the loader changes. The
    console script is dsh-mloader.

    :param python: Interpreter of the virtual environment.
    :raises PythonEnvironmentError: If the wheel cannot be installed.
    """
    source = _SDK_SOURCE
    version = _sdk_version(source)
    state = _pip_state(python)
    target = os.path.join(state["purelib"], _SDK_IMPORT_NAME)
    if (
        state["version"] == version
        and os.path.isdir(target)
        and _console_script_ready(python)
    ):
        return

    # The installed console script also imports this package, so an environment
    # that runs the installed copy cannot reinstall itself. It falls back to
    # repairing the console script only.
    if os.path.abspath(source) != os.path.abspath(target):
        directory = tempfile.mkdtemp(prefix="mloader-sdk-")
        try:
            wheel = _build_sdk_wheel(source, version, directory)
            _install_wheel(python, wheel)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    if not _console_script_ready(python):
        _write_console_script(python)
    if not _console_script_ready(python):
        raise PythonEnvironmentError(
            f"pip did not create the {_CONSOLE_SCRIPT_NAME} command in "
            f"{os.path.dirname(python)}"
        )
    _remove_legacy_console_script(python)


def _sdk_version(source: str) -> str:
    """Return the wheel version of one runtime source tree.

    The local version segment is a fingerprint, so a changed runtime
    produces a new version that pip installs over the previous one.

    :param source: Runtime package directory.
    :returns: PEP 440 version including the local segment.
    :rtype: str
    """
    return __version__ + "+" + _sdk_fingerprint(source)[:16]


def _sdk_fingerprint(source: str) -> str:
    """Hash the files that make up the runtime package.

    :param source: Runtime package directory.
    :returns: Lowercase hexadecimal SHA-256 digest.
    :rtype: str
    """
    digest = hashlib.sha256()
    for name, data in _sdk_files(source).items():
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _sdk_files(source: str) -> dict[str, bytes]:
    """Read the files of the runtime package as wheel archive members.

    :param source: Runtime package directory.
    :returns: Map from wheel member path to file bytes.
    :rtype: dict
    """
    result: dict[str, bytes] = {}
    for root, directories, files in os.walk(source):
        directories[:] = sorted(
            name for name in directories if name not in ("__pycache__", ".venv", ".git")
        )
        for name in sorted(files):
            if name.endswith(".pyc"):
                continue
            absolute = os.path.join(root, name)
            relative = os.path.relpath(absolute, source).replace(os.sep, "/")
            with open(absolute, "rb") as handle:
                result[f"{_SDK_IMPORT_NAME}/{relative}"] = handle.read()
    return result


def _build_sdk_wheel(source: str, version: str, directory: str) -> str:
    """Build a pure Python wheel for the runtime package.

    :param source: Runtime package directory.
    :param version: Version including the source fingerprint.
    :param directory: Directory that receives the wheel file.
    :returns: Absolute wheel path.
    :rtype: str
    :raises OSError: If a source file or the wheel cannot be written.
    """
    files = _sdk_files(source)
    dist_info = f"{_SDK_IMPORT_NAME}-{version}.dist-info"
    files[f"{dist_info}/METADATA"] = (
        "Metadata-Version: 2.1\n"
        f"Name: {_SDK_DIST_NAME}\n"
        f"Version: {version}\n"
        "Summary: Python runtime and plugin packaging command line for dsh-mloader\n"
        "Requires-Python: >=3.10\n"
        "License: MIT\n"
    ).encode()
    files[f"{dist_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\n"
        b"Generator: dsh-mloader\n"
        b"Root-Is-Purelib: true\n"
        b"Tag: py3-none-any\n"
    )
    files[f"{dist_info}/entry_points.txt"] = (
        f"[console_scripts]\n{_CONSOLE_SCRIPT_NAME} = {_SDK_IMPORT_NAME}.cli:main\n"
    ).encode()
    files[f"{dist_info}/top_level.txt"] = (_SDK_IMPORT_NAME + "\n").encode()

    record: list[str] = []
    for name in sorted(files):
        data = files[name]
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=")
        record.append(f"{name},sha256={digest.decode('ascii')},{len(data)}")
    record.append(f"{dist_info}/RECORD,,")
    files[f"{dist_info}/RECORD"] = ("\n".join(record) + "\n").encode("utf-8")

    wheel = os.path.join(
        directory,
        f"{_SDK_IMPORT_NAME}-{version}-py3-none-any.whl",
    )
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    return wheel


def _pip_state(python: str) -> dict[str, str]:
    """Read the environment purelib directory and the installed SDK version.

    :param python: Interpreter of the virtual environment.
    :returns: The purelib path and the installed version, empty when
        absent.
    :rtype: dict
    :raises PythonEnvironmentError: If the interpreter cannot report
        them.
    """
    code = "\n".join(
        [
            "import importlib.metadata as metadata",
            "import sysconfig",
            "try:",
            f"    version = metadata.version('{_SDK_DIST_NAME}')",
            "except Exception:",
            "    version = ''",
            "print(sysconfig.get_paths()['purelib'])",
            "print(version)",
        ]
    )
    result = _run([python, "-c", code], timeout=_VENV_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise PythonEnvironmentError(
            f"cannot inspect the environment at {os.path.dirname(python)}: "
            f"{_failure_detail(result)}"
        )
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        raise PythonEnvironmentError(
            f"the environment at {os.path.dirname(python)} did not report its paths"
        )
    return {"purelib": lines[0].strip(), "version": lines[1].strip()}


def _ensure_pip(python: str) -> None:
    """Make sure the environment interpreter has pip.

    :param python: Interpreter of the virtual environment.
    :raises PythonEnvironmentError: If pip cannot be made available.
    """
    result = _run([python, "-m", "pip", "--version"], timeout=_VENV_TIMEOUT_SECONDS)
    if result.returncode == 0:
        return
    upgrade = _run(
        [python, "-m", "ensurepip", "--upgrade"],
        timeout=_INSTALL_TIMEOUT_SECONDS,
    )
    if upgrade.returncode != 0:
        raise PythonEnvironmentError(
            f"the environment at {os.path.dirname(python)} has no pip: "
            f"{_failure_detail(upgrade)}"
        )


def _install_wheel(python: str, wheel: str) -> None:
    """Install one local wheel without resolving dependencies.

    :param python: Interpreter of the virtual environment.
    :param wheel: Absolute wheel path.
    :raises PythonEnvironmentError: If pip rejects the wheel.
    """
    _ensure_pip(python)
    result = _run(
        [
            python,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-deps",
            "--force-reinstall",
            wheel,
        ],
        timeout=_INSTALL_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        raise PythonEnvironmentError(
            f"pip could not install the dsh-mloader SDK: {_failure_detail(result)}"
        )


def _console_script_ready(python: str) -> bool:
    """Return true when the environment holds the dsh-mloader-sdk command.

    :param python: Interpreter of the virtual environment.
    :returns: Whether the console script exists.
    :rtype: bool
    """
    directory = os.path.dirname(python)
    if os.name == "nt":
        return os.path.isfile(
            os.path.join(directory, _CONSOLE_SCRIPT_NAME + ".exe")
        ) or os.path.isfile(os.path.join(directory, _CONSOLE_SCRIPT_NAME + ".cmd"))
    return os.path.isfile(os.path.join(directory, _CONSOLE_SCRIPT_NAME))


def _remove_legacy_console_script(python: str) -> None:
    """Remove the earlier dsh-mloader command from an environment.

    The command was renamed to dsh-mloader-sdk so the executable matches
    the argparse program name. The removal is best effort because a
    running or locked launcher may not be removable on every platform.

    :param python: Interpreter of the virtual environment.
    """
    directory = os.path.dirname(python)
    candidates = [os.path.join(directory, _LEGACY_CONSOLE_SCRIPT_NAME)]
    if os.name == "nt":
        candidates.extend(
            [
                os.path.join(directory, _LEGACY_CONSOLE_SCRIPT_NAME + ".exe"),
                os.path.join(directory, _LEGACY_CONSOLE_SCRIPT_NAME + ".cmd"),
            ]
        )
    for path in candidates:
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def _write_console_script(python: str) -> None:
    """Write a small launcher when pip did not create the console script.

    :param python: Interpreter of the virtual environment.
    :raises OSError: If the launcher cannot be written.
    """
    directory = os.path.dirname(python)
    os.makedirs(directory, exist_ok=True)
    module = _SDK_IMPORT_NAME
    if os.name == "nt":
        path = os.path.join(directory, _CONSOLE_SCRIPT_NAME + ".cmd")
        content = '@echo off\r\n"' + python + '" -m ' + module + " %*\r\n"
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        return
    path = os.path.join(directory, _CONSOLE_SCRIPT_NAME)
    content = "#!/bin/sh\nexec " + _quote_posix(python) + " -m " + module + ' "$@"\n'
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, 0o755)


def _quote_posix(value: str) -> str:
    """Quote one value for a POSIX shell command.

    :param value: Value to quote.
    :returns: The value wrapped in single quotes.
    :rtype: str
    """
    return "'" + value.replace("'", "'\\''") + "'"


def sync_plugin_environment(
    profile_dir: str,
    plugin_dir: str,
    python_bin: str = "python3",
) -> dict[str, Any]:
    """Prepare the shared environment for one plugin and install its
    dependencies.

    A plugin with pyproject.toml has its dependencies installed with uv.
    A plugin with only requirements.txt has them installed with the venv
    pip of the shared environment, and reports a warning. A plugin with
    neither file still gets the shared environment, so the backend has
    one interpreter to start.

    :param profile_dir: Absolute profile directory.
    :param plugin_dir: Absolute plugin directory.
    :param python_bin: Interpreter used when the environment must be
        created.
    :returns: The environment interpreter, its directory, the management
        mode, the dependencies that were requested and the warnings.
    :rtype: dict
    :raises PythonEnvironmentError: If the environment or a dependency
        cannot be prepared.
    """
    plugin_dir = os.path.abspath(plugin_dir)
    if not os.path.isdir(plugin_dir):
        raise PythonEnvironmentError(f"plugin directory not found: {plugin_dir}")

    warnings: list[str] = []
    pyproject = os.path.join(plugin_dir, PROJECT_FILE)
    requirements = os.path.join(plugin_dir, REQUIREMENTS_FILE)
    has_project = os.path.isfile(pyproject)
    has_requirements = os.path.isfile(requirements)

    project_dependencies: list[str] = []
    requires_python: str | None = None
    if has_project:
        try:
            with open(pyproject, "r", encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            raise PythonEnvironmentError(f"cannot read {pyproject}: {exc}") from exc
        project_dependencies, requires_python = read_project(text, pyproject)
        if requires_python:
            warnings.append(
                f"pyproject.toml declares requires-python = {requires_python!r}; "
                "Python version selection is not implemented, so the declaration is ignored"
            )

    requirement_entries = read_requirements(requirements) if has_requirements else []
    legacy = has_requirements and not has_project
    if legacy:
        warnings.append(
            "requirements.txt was found without pyproject.toml; its packages are "
            "installed with Python's venv pip instead of uv. Add a [project] table "
            "with a dependencies key to pyproject.toml to use uv"
        )

    python = ensure_shared_venv(
        profile_dir,
        python_bin,
        python_venv=legacy,
    )

    dependencies: list[str] = []
    if has_project:
        dependencies = list(project_dependencies)
        dependencies.extend(
            extra_requirements(project_dependencies, requirement_entries)
        )
        if dependencies:
            _install_with_uv(python, dependencies, plugin_dir)
        mode = "uv"
    elif has_requirements:
        if requirement_entries:
            _install_with_pip(python, requirements)
        mode = "python-venv"
    else:
        mode = "none"

    return {
        "python": python,
        "venv": shared_venv_dir(profile_dir),
        "mode": mode,
        "dependencies": dependencies,
        "requiresPython": requires_python,
        "warnings": warnings,
    }


def _install_with_uv(python: str, dependencies: list[str], plugin_dir: str) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise PythonEnvironmentError(
            "uv is required to install plugin dependencies; install uv and make "
            "sure it is on PATH"
        )
    handle, path = tempfile.mkstemp(prefix="mloader-requirements-", suffix=".txt")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            for dependency in dependencies:
                stream.write(dependency + "\n")
        result = _run(
            [uv, "pip", "install", "--python", python, "--no-progress", "-r", path],
            cwd=plugin_dir,
            timeout=_INSTALL_TIMEOUT_SECONDS,
        )
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
    if result.returncode != 0:
        raise PythonEnvironmentError(
            f"uv could not install the dependencies of {plugin_dir}: {_failure_detail(result)}"
        )


def _install_with_pip(python: str, requirements: str) -> None:
    command = [
        python,
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "-r",
        requirements,
    ]
    result = _run(command, timeout=_INSTALL_TIMEOUT_SECONDS)
    if result.returncode != 0 and _mentions_missing_pip(result):
        upgrade = _run(
            [python, "-m", "ensurepip", "--upgrade"],
            timeout=_INSTALL_TIMEOUT_SECONDS,
        )
        if upgrade.returncode != 0:
            raise PythonEnvironmentError(
                f"the environment at {os.path.dirname(python)} has no pip: "
                f"{_failure_detail(upgrade)}"
            )
        result = _run(command, timeout=_INSTALL_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise PythonEnvironmentError(
            f"pip could not install {requirements}: {_failure_detail(result)}"
        )


def _mentions_missing_pip(result: subprocess.CompletedProcess[str]) -> bool:
    text = f"{result.stderr}\n{result.stdout}".lower()
    return "no module named pip" in text


def _run(
    command: list[str],
    cwd: str | None = None,
    timeout: int = _VENV_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            check=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise PythonEnvironmentError(f"cannot run {command[0]}: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise PythonEnvironmentError(
            f"{command[0]} timed out after {timeout} seconds"
        ) from exc
    except OSError as exc:
        raise PythonEnvironmentError(f"cannot run {command[0]}: {exc}") from exc


def _failure_detail(result: subprocess.CompletedProcess[str]) -> str:
    text = (result.stderr or result.stdout or "").strip()
    if not text:
        return f"exit status {result.returncode}"
    lines = text.splitlines()
    return "\n".join(lines[-8:])
