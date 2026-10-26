# SPDX-License-Identifier: MIT
"""The host context projected onto a plugin.

A plugin reaches exactly four capability groups, all exposed by :class:`Context`:
``tools``, ``on``, ``logger`` and ``effect``. The projection is a whitelist.
Host services outside this list are not reachable from a plugin, which keeps the
Cordis service boundary of the loader intact.
"""

from __future__ import annotations

import sys
import traceback
from typing import Any, Callable

from .channel import Channel
from .errors import HostUnavailable

_Handler = Callable[..., Any]
_Cleanup = Callable[[], None]


def _empty_object_schema() -> dict[str, Any]:
    """Return the object schema used when a tool declares no parameters.

    :returns: A fresh JSON Schema subset that accepts an empty object.
    :rtype: dict
    """
    return {"type": "object", "properties": {}, "additionalProperties": False}


class _RuntimeState:
    """Mutable state shared by the runtime and the context namespaces.

    The state exists once per plugin process. The runtime fills the channel and
    the context during boot, and the namespaces write their registries into it.
    """

    def __init__(self) -> None:
        self.channel: Channel | None = None
        self.context: Context | None = None
        self.apply_fn: _Handler | None = None
        self.tool_handlers: dict[str, _Handler] = {}
        self.event_handlers: dict[str, _Handler] = {}
        self.cleanups: list[_Cleanup] = []
        self.declared_tools: list[dict[str, Any]] = []


state = _RuntimeState()


class ToolNamespace:
    """The ``ctx.tools`` capability.

    :param runtime_state: State owned by the running plugin process.
    """

    def __init__(self, runtime_state: _RuntimeState) -> None:
        self._state = runtime_state

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> Callable[[_Handler], _Handler]:
        """Register a tool and return a decorator that attaches its handler.

        The registration is announced to the host with a blocking
        ``tools.register`` request, so a rejected registration raises instead of
        leaving the plugin half mounted. The handler is callable until the
        plugin process exits.

        :param name: Tool name exposed to the model.
        :param description: Human readable description shown with the tool.
        :param parameters: JSON Schema subset describing the arguments. It
            defaults to an object schema with no properties.
        :param output: Optional JSON Schema subset describing the result.
        :param timeout_ms: Optional per call timeout in milliseconds.
        :returns: A decorator that records the decorated function as the tool
            handler and returns it unchanged.
        :rtype: collections.abc.Callable
        :raises PluginCallError: If the host rejects the registration.
        :raises HostUnavailable: If the host channel is closed.
        """

        def decorator(fn: _Handler) -> _Handler:
            spec: dict[str, Any] = {
                "name": name,
                "description": description,
                "parameters": parameters or _empty_object_schema(),
            }
            if output is not None:
                spec["output"] = output
            if timeout_ms is not None:
                spec["timeoutMs"] = timeout_ms
            self._state.channel.request("tools.register", {"tool": spec})
            self._state.tool_handlers[spec["name"]] = fn
            return fn

        return decorator


class EventNamespace:
    """The ``ctx.on`` capability.

    A subscription is announced to the host with a blocking ``events.on``
    request, so an event outside the host whitelist raises and the plugin learns
    about it during apply.

    :param runtime_state: State owned by the running plugin process.
    """

    def __init__(self, runtime_state: _RuntimeState) -> None:
        self._state = runtime_state

    def __call__(self, event: str) -> Callable[[_Handler], _Handler]:
        """Subscribe a handler to a host event.

        :param event: Whitelisted event name.
        :returns: A decorator that records the decorated function as the
            handler.
        :rtype: collections.abc.Callable
        """
        return self.on(event)

    def on(self, event: str) -> Callable[[_Handler], _Handler]:
        """Subscribe a handler to a host event.

        A later subscription to the same event replaces the earlier handler.

        :param event: Whitelisted event name.
        :returns: A decorator that records the decorated function as the
            handler and returns it unchanged.
        :rtype: collections.abc.Callable
        :raises PluginCallError: If the host rejects the subscription.
        :raises HostUnavailable: If the host channel is closed.
        """

        def decorator(fn: _Handler) -> _Handler:
            self._state.channel.request("events.on", {"event": event})
            self._state.event_handlers[event] = fn
            return fn

        return decorator


class LoggerNamespace:
    """The ``ctx.logger`` capability.

    Logging is a one way notification. The runtime never waits for a host reply,
    so a plugin can log from inside an event handler while the host is blocked
    dispatching that event. A log entry that cannot be delivered is dropped and
    never raises.

    :param runtime_state: State owned by the running plugin process.
    """

    def __init__(self, runtime_state: _RuntimeState) -> None:
        self._state = runtime_state

    def debug(self, message: Any, data: Any = None) -> None:
        """Log a debug level message.

        :param message: Message text, converted with ``str``.
        :param data: Optional JSON serialisable detail attached to the entry.
        """
        self._log("debug", message, data)

    def info(self, message: Any, data: Any = None) -> None:
        """Log an info level message.

        :param message: Message text, converted with ``str``.
        :param data: Optional JSON serialisable detail attached to the entry.
        """
        self._log("info", message, data)

    def warn(self, message: Any, data: Any = None) -> None:
        """Log a warning level message.

        :param message: Message text, converted with ``str``.
        :param data: Optional JSON serialisable detail attached to the entry.
        """
        self._log("warn", message, data)

    warning = warn

    def error(self, message: Any, data: Any = None) -> None:
        """Log an error level message.

        :param message: Message text, converted with ``str``.
        :param data: Optional JSON serialisable detail attached to the entry.
        """
        self._log("error", message, data)

    def _log(self, level: str, message: Any, data: Any = None) -> None:
        payload: dict[str, Any] = {"level": level, "message": str(message)}
        if data is not None:
            payload["data"] = data
        channel = self._state.channel
        if channel is None:
            return
        try:
            channel.notify("logger.log", payload)
        except Exception:  # noqa: BLE001 - a log entry must never break the plugin
            pass


class EffectNamespace:
    """The ``ctx.effect`` capability.

    An effect is announced to the host with a blocking ``effect.register``
    request, and its cleanup runs once when the plugin is disposed.

    :param runtime_state: State owned by the running plugin process.
    """

    def __init__(self, runtime_state: _RuntimeState) -> None:
        self._state = runtime_state

    def register(self, label: str, cleanup: _Cleanup | None = None) -> None:
        """Register a side effect with an optional cleanup function.

        :param label: Effect name reported to the host.
        :param cleanup: Function called on dispose, or ``None`` when the effect
            needs no cleanup. Cleanups run in reverse registration order.
        :raises PluginCallError: If the host rejects the registration.
        :raises HostUnavailable: If the host channel is closed.
        """
        if cleanup is not None:
            self._state.cleanups.append(cleanup)
        self._state.channel.request("effect.register", {"label": label})

    def run_cleanups(self) -> None:
        """Run every registered cleanup in last in first out order.

        An exception in one cleanup is printed on standard error and the
        remaining cleanups still run.
        """
        while self._state.cleanups:
            cleanup = self._state.cleanups.pop()
            try:
                cleanup()
            except Exception:  # noqa: BLE001 - one cleanup must not block the rest
                traceback.print_exc(file=sys.stderr)


class Context:
    """The four capability namespaces handed to ``apply(ctx)``.

    :param runtime_state: State owned by the running plugin process.
    """

    def __init__(self, runtime_state: _RuntimeState) -> None:
        self.tools = ToolNamespace(runtime_state)
        self.on = EventNamespace(runtime_state)
        self.logger = LoggerNamespace(runtime_state)
        self.effect = EffectNamespace(runtime_state)


class _ContextProxy:
    """Late bound alias for the active context.

    ``from dsh_mloader_sdk import ctx`` binds this proxy at import time, before
    the runtime exists. Every attribute lookup is forwarded to the context that
    boot installed.
    """

    def __getattr__(self, name: str) -> Any:
        context = state.context
        if context is None:
            raise HostUnavailable("ctx is not available before boot")
        return getattr(context, name)


ctx = _ContextProxy()


def get_context() -> Context | None:
    """Return the active context, or ``None`` when the runtime has not started.

    :returns: The context installed for the running plugin, if any.
    :rtype: Context or None
    """
    return state.context
