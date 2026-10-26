# SPDX-License-Identifier: MIT
"""Exceptions raised by the dsh-mloader Python runtime."""


class PluginCallError(RuntimeError):
    """The host rejected a request made by the plugin.

    Raised when a host reply carries ``ok: false``, which happens when the host
    refuses a registration or the plugin calls a capability it is not allowed to
    use. The message is the reason supplied by the host.

    :param message: Human readable rejection reason.
    """


class HostUnavailable(RuntimeError):
    """The host channel is closed and can no longer carry a request.

    Raised when a frame is written to or read from a channel whose peer is
    gone, and when :data:`dsh_mloader_sdk.ctx` is used before the runtime
    started. ``ctx.logger`` never raises this because it drops log entries that
    cannot be delivered.

    :param message: Description of the failed channel operation.
    """
