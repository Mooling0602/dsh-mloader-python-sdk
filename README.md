# dsh-mloader-python-sdk

Python SDK for dsh-mloader, the multi-runtime plugin loader for DeepSeek
Harness. A Python plugin imports `dsh_mloader_sdk` to declare tools, subscribe to
host events, write logs, and register cleanup functions.

## Generated source

The package source under `src/dsh-mloader-sdk/` is generated from the
`src/runtime/python_sdk` directory of the dsh-mloader repository. That
repository is the canonical source and this repository is a mirror, so change
the runtime in dsh-mloader and copy it here. This repository does not contain
the canonical implementation.

## Install

dsh-mloader ships the SDK and loads it for every Python plugin, so a plugin
installed into a profile does not install it separately. To work on the SDK
itself:

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

The test suite uses the `unittest` framework from the standard library, so it
needs no test dependency. The import name is `dsh_mloader_sdk`, never the
distribution name.

## Minimal plugin, style A

Declare an `apply` entry point and register capabilities on the context it
receives.

```python
from dsh_mloader_sdk import plugin

@plugin
def apply(ctx):
    @ctx.tools.register(
        name="greet",
        description="Greet one person by name.",
        parameters={
            "type": "object",
            "properties": {"who": {"type": "string"}},
            "required": ["who"],
            "additionalProperties": False,
        },
    )
    def greet(args):
        return {"text": f"hello {args['who']}"}

    ctx.effect.register("greet-cleanup", lambda: ctx.logger.info("bye"))
```

## Minimal plugin, style B

Declare tools without writing an `apply` function. The loader registers the
declared tools for the plugin.

```python
from dsh_mloader_sdk import tool

@tool(
    name="add",
    description="Add two numbers.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    },
)
def add(args):
    return {"sum": args["a"] + args["b"]}
```

Both styles may be mixed in one plugin. A manifest next to the plugin file
supplies the plugin identity and the entry path.

## License

MIT. See [LICENSE](LICENSE).
