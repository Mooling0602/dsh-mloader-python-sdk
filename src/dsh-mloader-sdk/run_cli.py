# SPDX-License-Identifier: MIT
"""Run the SDK command line from a checkout that has no installed package.

The runtime directory is named python_sdk while the import name is
dsh_mloader_sdk, so a plain python -m dsh_mloader_sdk run needs the
package to be loaded by path first. The dsh-mloader loader and a plugin
author run this
file:

python3 src/runtime/python_sdk/run_cli.py pack ./my-plugin
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from types import ModuleType

_PACKAGE_NAME = "dsh_mloader_sdk"
_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_ENTRY_FILE = os.path.join(_PACKAGE_DIR, "__init__.py")


def _load_package() -> ModuleType:
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
    """Load the package and hand the arguments to the command line.

    :param argv: Arguments without the program name.
    :returns: Process exit status.
    :rtype: int
    """
    _load_package()
    cli = importlib.import_module(f"{_PACKAGE_NAME}.cli")
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
