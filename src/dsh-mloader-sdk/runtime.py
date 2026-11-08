# SPDX-License-Identifier: MIT
"""The plugin process runtime.

The runtime owns one plugin process. It answers the ``boot`` and
``apply`` handshakes, registers the tools a plugin declares, dispatches
host requests to the handlers the plugin registered, and runs the
registered cleanups when the host disposes the plugin.

Plugins declare their entry point with :func:`plugin`, declare their
tools with :func:`tool`, or declare their commands with :func:`command`.
The styles may be mixed; declared tools are registered first, then
declared commands, then the entry function runs.
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
import traceback
from collections.abc import Callable, Sequence
from types import ModuleType
from typing import Any

from .channel import Channel
from .context import (
    Context,
    _empty_object_schema,
    _output_schema_from_annotation,
    state,
)
from .errors import HostUnavailable
from .stdio import detach

_PLUGIN_MODULE_NAME = "dsh_user_plugin"

_Handler = Callable[..., Any]


def plugin(fn: _Handler | None = None, *, name: str | None = None) -> Any:
    """Mark a function as the plugin entry point.

    The decorated function is called once with the context during the
    ``apply`` handshake. A module level ``apply`` function is used when
    no decorated entry exists; a decorated entry takes precedence over
    it.

    :param fn: Function to mark, supplied by the bare ``@plugin`` form.
    :param name: Accepted for readability only. The manifest is the
        authoritative source of plugin identity, so this argument has no
        effect.
    :returns: The decorated function unchanged, or a decorator when
        called without a function.
    :rtype: collections.abc.Callable
    """

    def decorate(func: _Handler) -> _Handler:
        state.apply_fn = func
        return func

    if fn is not None and callable(fn):
        return decorate(fn)
    return decorate


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any] | None = None,
    output: dict[str, Any] | None = None,
    timeout_ms: int | None = None,
) -> Callable[[_Handler], _Handler]:
    """Declare a tool without an ``apply`` function.

    The decorated functions are collected into a tool table that the
    runtime registers during the ``apply`` handshake, before the entry
    function runs. Tool handlers are called with the argument object the
    model supplied.

    :param name: Tool name exposed to the model.
    :param description: Human readable description shown with the tool.
    :param parameters: JSON Schema subset describing the arguments. It
        defaults to an object schema with no properties.
    :param output: Optional JSON Schema subset describing the result.
    :param timeout_ms: Optional per call timeout in milliseconds.
    :returns: A decorator that records the decorated function as a tool
        handler and returns it unchanged.
    :rtype: collections.abc.Callable
    """

    def decorator(fn: _Handler) -> _Handler:
        entry: dict[str, Any] = {
            "name": name,
            "description": description,
            "handler": fn,
        }
        if parameters is not None:
            entry["parameters"] = parameters
        if output is not None:
            entry["output"] = output
        if timeout_ms is not None:
            entry["timeoutMs"] = timeout_ms
        state.declared_tools.append(entry)
        return fn

    return decorator


def command(
    names: str | Sequence[str],
    description: str,
    input_hint: str | None = None,
    node_id: str | None = None,
) -> Callable[[_Handler], _Handler]:
    """Declare a command without an ``apply`` function.

    The decorated functions are collected into a command table that the
    runtime registers during the ``apply`` handshake, before the entry
    function runs. Candidate names are tried in order against the global
    command namespace; the first free name is registered. When every
    candidate is taken, the node is skipped and the plugin keeps loading.

    :param names: One candidate command name, or several in priority order.
    :param description: Human readable summary shown in the command list.
    :param input_hint: Optional placeholder for the command input.
    :param node_id: Stable id used to route execution back to the plugin.
        Defaults to the first candidate name.
    :returns: A decorator that records the decorated function as a command
        handler and returns it unchanged.
    :rtype: collections.abc.Callable
    """

    def decorator(fn: _Handler) -> _Handler:
        entry: dict[str, Any] = {
            "names": [names] if isinstance(names, str) else list(names),
            "description": description,
            "handler": fn,
        }
        if input_hint is not None:
            entry["input_hint"] = input_hint
        if node_id is not None:
            entry["node_id"] = node_id
        state.declared_commands.append(entry)
        return fn

    return decorator


def _load_plugin_module(plugin_path: str) -> ModuleType:
    """Load the plugin entry file under a fixed module name.

    :param plugin_path: Absolute path of the plugin entry file.
    :returns: The imported plugin module.
    :rtype: types.ModuleType
    :raises RuntimeError: If the file cannot be loaded as a Python
        module.
    """
    spec = importlib.util.spec_from_file_location(_PLUGIN_MODULE_NAME, plugin_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load plugin module from {plugin_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_PLUGIN_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _declared_tool_entries(module: ModuleType) -> list[dict[str, Any]]:
    """Return the tools declared by decorators and by a module level ``TOOLS``
    list.

    :param module: The loaded plugin module.
    :returns: Tool entries in declaration order, decorator entries
        first.
    :rtype: list of dict
    """
    entries = list(state.declared_tools)
    module_tools = getattr(module, "TOOLS", None)
    if isinstance(module_tools, list):
        entries.extend(module_tools)
    return entries


def _declared_command_entries(module: ModuleType) -> list[dict[str, Any]]:
    """Return the commands declared by decorators and by ``COMMANDS``.

    :param module: The loaded plugin module.
    :returns: Command entries in declaration order, decorator entries first.
    :rtype: list of dict
    """
    entries = list(state.declared_commands)
    module_commands = getattr(module, "COMMANDS", None)
    if isinstance(module_commands, list):
        entries.extend(module_commands)
    return entries


def _declared_command_spec(entry: dict[str, Any]) -> dict[str, Any]:
    """Build the registration payload for one declared command entry.

    :param entry: Command entry produced by :func:`command` or listed in
        ``COMMANDS``.
    :returns: Command specification sent to the host.
    :rtype: dict
    """
    spec: dict[str, Any] = {
        "names": entry["names"],
        "description": entry["description"],
    }
    if "node_id" in entry:
        spec["id"] = entry["node_id"]
    if "input_hint" in entry:
        spec["input"] = {"hint": entry["input_hint"]}
    return spec


def _normalize_command_result(value: Any) -> dict[str, Any]:
    """Normalize the value a command handler returned.

    A string becomes a successful text result. A dict must carry ``kind``
    and ``text``.

    :param value: Value returned by the handler.
    :returns: A command result object the host accepts.
    :rtype: dict
    :raises TypeError: If the value cannot be rendered as a command result.
    """
    if isinstance(value, str):
        return {"kind": "success", "text": value}
    if isinstance(value, dict) and value.get("kind") in ("success", "error"):
        text = value.get("text", "")
        if not isinstance(text, str):
            raise TypeError("command result text must be a string")
        if value["kind"] == "error" and not text.strip():
            raise TypeError("command error result requires non-empty text")
        return {"kind": value["kind"], "text": text}
    raise TypeError("command handler must return a string or a dict with kind and text")


def _declared_tool_spec(entry: dict[str, Any]) -> dict[str, Any]:
    """Build the registration payload for one declared tool entry.

    :param entry: Tool entry produced by :func:`tool` or listed in
        ``TOOLS``.
    :returns: Tool specification sent to the host.
    :rtype: dict
    """
    spec: dict[str, Any] = {
        "name": entry["name"],
        "description": entry["description"],
        "parameters": entry.get("parameters") or _empty_object_schema(),
    }
    declared_output = entry.get("output")
    if declared_output is None:
        declared_output = _output_schema_from_annotation(entry["handler"])
    if declared_output is not None:
        spec["output"] = declared_output
    if "timeoutMs" in entry:
        spec["timeoutMs"] = entry["timeoutMs"]
    return spec


def _handle_host_request(frame: dict[str, Any]) -> None:
    """Dispatch one host request that arrived on the channel.

    An ``event`` frame is forwarded to the handler subscribed to that
    event. An event with no handler is acknowledged with a null result.
    A handler that raises is reported to the host as a failed response,
    so the plugin process stays alive. A ``dispose`` frame runs the
    cleanups, acknowledges the host, and ends the process with status 0.

    :param frame: Host request frame.
    """
    channel = state.require_channel()
    op = frame.get("op")
    payload = frame.get("payload") or {}
    request_id = frame.get("id")

    if op == "event":
        handler = state.event_handlers.get(payload.get("event"))
        if handler is None:
            channel.respond(request_id, True, None)
            return
        args = payload.get("args") or []
        try:
            parameters = inspect.signature(handler).parameters.values()
            positional = [
                parameter
                for parameter in parameters
                if parameter.kind
                in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                )
            ]
            if any(
                parameter.kind == inspect.Parameter.VAR_POSITIONAL
                for parameter in parameters
            ):
                result = handler(*args)
            elif len(positional) == 0:
                result = handler()
            elif len(positional) == 1:
                result = handler(*args[:1])
            else:
                result = handler(*args)
            channel.respond(request_id, True, result)
        except Exception as exc:  # noqa: BLE001 - a handler error is a tool error
            channel.respond(request_id, False, error={"message": str(exc)})
        return

    if op == "dispose":
        context = state.context
        if context is None:
            raise HostUnavailable("plugin context is not available before boot")
        context.effect.run_cleanups()
        channel.respond(request_id, True, {"disposed": True})
        raise SystemExit(0)

    channel.respond(request_id, False, error={"message": f"unsupported host op {op}"})


def run(plugin_path: str) -> int:
    """Run the plugin process until the host disposes it or the channel closes.

    The handshake is ``boot``, then ``apply``, then a serve loop.
    :func:`stdio.detach` moves descriptors 0, 1 and 2 to null streams
    before the plugin module loads, so plugin code cannot write to the
    protocol or to the loader logger except through ``ctx.logger``. The
    returned private streams are the only access to the host for the
    lifetime of the process.

    :param plugin_path: Absolute path of the plugin entry file, resolved
        by ``launch.py``.
    :returns: Process exit status. ``0`` means the host closed the
        channel or disposed the plugin; a nonzero status means the
        handshake failed.
    :rtype: int
    """
    streams = detach()
    channel = Channel(streams.stdin, streams.stdout, streams.diagnostics)
    channel.on_host_request = _handle_host_request
    state.channel = channel
    state.diagnostics = streams.diagnostics
    state.context = Context(state)

    boot = channel.read_frame()
    if boot is None:
        return 0
    if boot.get("op") != "boot":
        channel.respond(boot.get("id"), False, error={"message": "expected boot"})
        return 1

    try:
        module = _load_plugin_module(plugin_path)
    except Exception as exc:  # noqa: BLE001 - an import error is a boot failure
        traceback.print_exc(file=streams.diagnostics)
        channel.respond(
            boot.get("id"), False, error={"message": f"import failed: {exc}"}
        )
        return 1

    plugin_id = (boot.get("payload") or {}).get("pluginId")
    channel.respond(
        boot.get("id"),
        True,
        {"pluginId": plugin_id, "python": sys.version.split()[0]},
    )

    apply_frame = channel.read_frame()
    if apply_frame is None:
        return 0
    if apply_frame.get("op") != "apply":
        channel.respond(
            apply_frame.get("id"), False, error={"message": "expected apply"}
        )
        return 1

    try:
        for entry in _declared_tool_entries(module):
            spec = _declared_tool_spec(entry)
            channel.request("tools.register", {"tool": spec})
            state.tool_handlers[spec["name"]] = entry["handler"]

        for entry in _declared_command_entries(module):
            spec = _declared_command_spec(entry)
            reply = channel.request("commands.register", {"command": spec})
            if isinstance(reply, dict) and reply.get("registered") is True:
                identifier = spec.get("id") or spec["names"][0]
                state.command_handlers[identifier] = entry["handler"]

        entry_fn = state.apply_fn or getattr(module, "apply", None)
        if callable(entry_fn):
            entry_fn(state.context)

        channel.respond(
            apply_frame.get("id"),
            True,
            {
                "tools": sorted(state.tool_handlers),
                "events": sorted(state.event_handlers),
            },
        )
    except Exception as exc:  # noqa: BLE001 - report an apply failure and stop
        traceback.print_exc(file=streams.diagnostics)
        channel.respond(apply_frame.get("id"), False, error={"message": str(exc)})
        return 1

    while True:
        frame = channel.read_frame()
        if frame is None:
            return 0
        op = frame.get("op")
        payload = frame.get("payload") or {}
        request_id = frame.get("id")

        if op == "tool.call":
            handler = state.tool_handlers.get(payload.get("tool"))
            if handler is None:
                channel.respond(
                    request_id,
                    False,
                    error={"message": f"unknown tool {payload.get('tool')!r}"},
                )
                continue
            try:
                channel.respond(request_id, True, handler(payload.get("args") or {}))
            except Exception as exc:  # noqa: BLE001 - a tool error is not a crash
                channel.respond(request_id, False, error={"message": str(exc)})
        elif op == "command.call":
            handler = state.command_handlers.get(payload.get("command"))
            if handler is None:
                channel.respond(
                    request_id,
                    False,
                    error={"message": f"unknown command node {payload.get('command')!r}"},
                )
                continue
            try:
                result = _normalize_command_result(handler(payload.get("input") or ""))
                channel.respond(request_id, True, result)
            except Exception as exc:  # noqa: BLE001 - a command error is not a crash
                channel.respond(request_id, False, error={"message": str(exc)})
        elif op == "event":
            _handle_host_request(frame)
        elif op == "dispose":
            state.context.effect.run_cleanups()
            channel.respond(request_id, True, {"disposed": True})
            return 0
        else:
            channel.respond(
                request_id, False, error={"message": f"unsupported op {op}"}
            )
