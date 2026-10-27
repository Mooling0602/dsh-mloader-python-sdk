# SPDX-License-Identifier: MIT
"""Process entry point used by the dsh-mloader Python backend.

The loader starts this file with the plugin entry path::

    python3 -u launch.py /absolute/path/to/plugin.py

The working directory is the plugin base directory and standard output carries
the protocol, so this file writes diagnostics to standard error only. It loads
the runtime directory as the ``dsh_mloader_sdk`` package, adds the plugin
directory to ``sys.path`` so the plugin may import local modules, and hands
control to the runtime.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from types import ModuleType

_PACKAGE_NAME = "dsh_mloader_sdk"
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENTRY_FILE = os.path.join(_PACKAGE_DIR, "__init__.py")


def _use_utf8_standard_streams() -> None:
    """Switch the standard streams to UTF-8.

    The loader encodes its requests and decodes both output streams as UTF-8.
    An inherited PYTHONIOENCODING setting or an ASCII locale with the UTF-8 mode
    disabled would otherwise select the codec of this process instead, and the
    first non-ASCII character would raise inside a read or a write. An
    undecodable byte becomes the replacement character, which leaves a line the
    runtime skips, and a character that UTF-8 cannot carry is written as an
    escape sequence, which keeps a frame well formed.

    :returns: Nothing.
    :rtype: None
    """
    settings = (
        (sys.stdin, "replace"),
        (sys.stdout, "backslashreplace"),
        (sys.stderr, "backslashreplace"),
    )
    for stream, errors in settings:
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors=errors)


def _load_runtime() -> ModuleType:
    """Import the runtime package from the directory that contains this file.

    The package is loaded by path rather than by a plain import so the loader
    runs the copy that ships beside this file, even when another
    ``dsh_mloader_sdk`` is installed in the interpreter environment.

    :returns: The imported ``dsh_mloader_sdk`` package.
    :rtype: types.ModuleType
    :raises RuntimeError: If the package directory has no importable loader.
    """
    loaded = sys.modules.get(_PACKAGE_NAME)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        _PACKAGE_NAME,
        _ENTRY_FILE,
        submodule_search_locations=[_PACKAGE_DIR],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load the python runtime from {_PACKAGE_DIR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PACKAGE_NAME] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    """Start the runtime for the plugin entry named on the command line.

    :param argv: Command line arguments. ``argv[1]`` is the plugin entry path,
        resolved against the current working directory.
    :returns: Process exit status.
    :rtype: int
    """
    _use_utf8_standard_streams()
    if len(argv) < 2:
        print("usage: launch.py <plugin.py>", file=sys.stderr)
        return 2
    plugin_path = os.path.abspath(argv[1])
    if not os.path.isfile(plugin_path):
        print(f"plugin file not found: {plugin_path}", file=sys.stderr)
        return 2
    plugin_dir = os.path.dirname(plugin_path)
    if plugin_dir not in sys.path:
        sys.path.insert(0, plugin_dir)

    _load_runtime()
    runtime = importlib.import_module(f"{_PACKAGE_NAME}.runtime")
    return runtime.run(plugin_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
