# SPDX-License-Identifier: MIT
"""Public API of the dsh-mloader Python runtime.

A plugin imports the declaration helpers from this package. The runtime injects
the context into the entry function of the Cordis aligned style::

    from dsh_mloader_sdk import plugin

    @plugin
    def apply(ctx):
        @ctx.tools.register(name="greet", description="Say hello")
        def greet(args):
            return {"text": f"hello {args['who']}"}

The declarative style skips ``apply`` and registers the decorated handlers
directly::

    from dsh_mloader_sdk import tool

    @tool(name="add", description="Add two numbers")
    def add(args):
        return {"sum": args["a"] + args["b"]}

Both styles may be mixed in one plugin. The wire protocol is version 1 and is
specified in ``docs/ABI-unified.md`` and ``docs/python-sdk/PyABI.md`` of the
dsh-mloader repository.
"""

from .context import Context, ctx, get_context
from .errors import HostUnavailable, PluginCallError
from .runtime import plugin, tool
from .version import PROTOCOL_VERSION, __version__

__all__ = [
    "PROTOCOL_VERSION",
    "Context",
    "HostUnavailable",
    "PluginCallError",
    "__version__",
    "ctx",
    "get_context",
    "plugin",
    "tool",
]
