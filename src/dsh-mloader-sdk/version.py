# SPDX-License-Identifier: MIT
"""Version identifiers of the dsh-mloader Python runtime.

The package version tracks the dsh-mloader release that ships this
runtime. The protocol version is separate: it changes only when the
message shapes on the wire change, and it is part of the contract in
``docs/ABI-unified.md``.
"""

__version__ = "0.1.0"

PROTOCOL_VERSION = 1
