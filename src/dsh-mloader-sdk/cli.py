# SPDX-License-Identifier: MIT
"""Command line front end of the dsh-mloader Python runtime.

The command packs a plugin directory, installs or updates a package under a dsh
profile, removes an installed plugin, and prepares the shared Python
environment. The loader calls the environment subcommand before it starts a
Python plugin; a plugin author and an operator use the other commands directly.

Usage examples:

    python3 run_cli.py pack ./my-plugin
    python3 run_cli.py install ./my-plugin-0.1.0.mloader.pack --profile ~/.dsh/profiles/web
    python3 run_cli.py remove my-plugin --profile ~/.dsh/profiles/web --yes
    python3 run_cli.py env sync --profile ~/.dsh/profiles/web --plugin ~/.dsh/profiles/web/mloader/plugins/my-plugin
    python3 run_cli.py env ensure --profile ~/.dsh/profiles/web
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from . import packaging
from .dependencies import DependencyError
from .environment import (
    PythonEnvironmentError,
    ensure_shared_venv,
    resolve_profile_dir,
    shared_venv_dir,
    sync_plugin_environment,
)
from .version import __version__


def main(argv: list[str] | None = None) -> int:
    """Run the command line.

    :param argv: Arguments without the program name. Defaults to sys.argv[1:].
    :returns: Process exit status. Zero means success.
    :rtype: int
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "profile", None) is None and hasattr(args, "profile"):
        args.profile = _active_profile()
    try:
        return _dispatch(args)
    except (packaging.PackageError, PythonEnvironmentError, DependencyError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dsh-mloader-sdk",
        description="Pack, install and remove dsh-mloader Python plugins, and prepare "
        "the shared Python environment of a dsh profile.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    pack_parser = subparsers.add_parser("pack", help="build a plugin package")
    pack_parser.add_argument("plugin_dir", help="plugin directory")
    pack_parser.add_argument("-o", "--output", help="archive path")
    pack_parser.add_argument("--force", action="store_true", help="overwrite the archive")
    pack_parser.add_argument("--json", action="store_true", help="print JSON")
    pack_parser.set_defaults(handler=_pack)

    install_parser = subparsers.add_parser("install", help="install or update a package")
    install_parser.add_argument("archive", help="package file")
    install_parser.add_argument("--profile", help="dsh profile directory")
    install_parser.add_argument("--plugins-dir", help="override the plugin directory")
    install_parser.add_argument("--python", default=sys.executable, help="base Python interpreter")
    install_parser.add_argument("--force", action="store_true", help="replace an existing installation")
    install_parser.add_argument("--json", action="store_true", help="print JSON")
    install_parser.set_defaults(handler=_install)

    remove_parser = subparsers.add_parser("remove", help="remove an installed plugin")
    remove_parser.add_argument("plugin_id", help="plugin id")
    remove_parser.add_argument("--profile", help="dsh profile directory")
    remove_parser.add_argument("--plugins-dir", help="override the plugin directory")
    remove_parser.add_argument("--yes", action="store_true", help="confirm the removal")
    remove_parser.add_argument("--json", action="store_true", help="print JSON")
    remove_parser.set_defaults(handler=_remove)

    env_parser = subparsers.add_parser("env", help="prepare the shared environment")
    env_subparsers = env_parser.add_subparsers(dest="env_command", required=True)

    ensure_parser = env_subparsers.add_parser("ensure", help="create the shared environment")
    ensure_parser.add_argument("--profile", help="dsh profile directory")
    ensure_parser.add_argument("--python", default=sys.executable, help="base Python interpreter")
    ensure_parser.add_argument("--json", action="store_true", help="print JSON")
    ensure_parser.set_defaults(handler=_env_ensure)

    sync_parser = env_subparsers.add_parser("sync", help="prepare the environment for one plugin")
    sync_parser.add_argument("--profile", help="dsh profile directory")
    sync_parser.add_argument("--plugin", required=True, help="plugin directory")
    sync_parser.add_argument("--python", default=sys.executable, help="base Python interpreter")
    sync_parser.add_argument("--json", action="store_true", help="print JSON")
    sync_parser.set_defaults(handler=_env_sync)

    return parser


def _active_profile() -> str | None:
    """Return the profile that owns the active virtual environment.

    The environment is expected at <profile>/mloader/.venv. Running the
    dsh-mloader-sdk command from an activated environment therefore needs no
    --profile argument, and a --profile argument still wins when supplied.

    :returns: Absolute profile directory, or None when the environment does not
        sit under a profile.
    :rtype: str or None
    """
    if sys.prefix != sys.base_prefix:
        venv = sys.prefix
    else:
        venv = os.environ.get("VIRTUAL_ENV") or ""
    if not venv:
        return None
    path = os.path.abspath(os.path.expanduser(venv))
    if os.path.basename(path) != ".venv":
        return None
    mloader = os.path.dirname(path)
    if os.path.basename(mloader) != "mloader":
        return None
    profile = os.path.dirname(mloader)
    return profile if os.path.isdir(profile) else None


def _dispatch(args: argparse.Namespace) -> int:
    return args.handler(args)


def _pack(args: argparse.Namespace) -> int:
    result = packaging.pack(args.plugin_dir, output=args.output, force=args.force)
    _print_warnings(result.get("warnings", []))
    if args.json:
        _print_json(result)
    else:
        print(f"packed {result['path']}")
        print(f"  id: {result['id']} {result['version']}")
        print(f"  md5: {result['md5']}")
        print(f"  entries: {result['entries']}")
    return 0


def _install(args: argparse.Namespace) -> int:
    result = packaging.install(
        args.archive,
        profile_dir=args.profile,
        plugins_dir=args.plugins_dir,
        force=args.force,
        python_bin=args.python,
    )
    _print_warnings(result.get("warnings", []))
    if args.json:
        _print_json(result)
    else:
        action = "updated" if result["updated"] else "installed"
        print(f"{action} {result['id']} {result['version']} at {result['directory']}")
        environment = result.get("environment")
        if environment:
            print(f"  python: {environment['python']}")
        if result.get("requirementsMerged"):
            print(f"  merged: {', '.join(result['requirementsMerged'])}")
    return 0


def _remove(args: argparse.Namespace) -> int:
    result = packaging.remove(
        args.plugin_id,
        profile_dir=args.profile,
        plugins_dir=args.plugins_dir,
        force=args.yes,
    )
    if args.json:
        _print_json(result)
    else:
        print(f"removed {result['id']} to {result['trash']}")
    return 0


def _env_ensure(args: argparse.Namespace) -> int:
    profile = resolve_profile_dir(args.profile)
    python = ensure_shared_venv(profile, args.python)
    result: dict[str, Any] = {
        "python": python,
        "venv": shared_venv_dir(profile),
        "warnings": [],
    }
    _print_warnings(result["warnings"])
    if args.json:
        _print_json(result)
    else:
        print(f"venv: {result['venv']}")
        print(f"python: {result['python']}")
    return 0


def _env_sync(args: argparse.Namespace) -> int:
    profile = resolve_profile_dir(args.profile)
    result = sync_plugin_environment(profile, args.plugin, python_bin=args.python)
    _print_warnings(result.get("warnings", []))
    if args.json:
        _print_json(result)
    else:
        print(f"plugin: {args.plugin}")
        print(f"venv: {result['venv']}")
        print(f"python: {result['python']}")
        print(f"mode: {result['mode']}")
        if result["dependencies"]:
            print(f"dependencies: {', '.join(result['dependencies'])}")
    return 0


def _print_warnings(warnings: list[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)


def _print_json(result: dict[str, Any]) -> None:
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
